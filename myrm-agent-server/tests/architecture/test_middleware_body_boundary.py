"""Architecture gate: a middleware that touches a request body, the receive channel or a websocket is pure ASGI.

Starlette's BaseHTTPMiddleware keeps the body it reads on the request object and hands that copy to the layers
below, wraps ``receive`` in its own bookkeeping, and passes websocket scopes through untouched. A middleware that
reads or replaces a body there reads a different body from the one the route reads, breaks streaming responses on
servers that announce ASGI 2.3, or never sees the connection it means to guard. The two middlewares that rewrite
bodies (E2EE decryption, text sanitizing) are pure ASGI for that reason; this gate keeps it so.

[INPUT]
- app/**/*.py: every module that defines a class derived from BaseHTTPMiddleware

[OUTPUT]
- that such a module never reads or replaces a request body, never touches ``receive``, and never handles a websocket

[POS]
The one place that stops a new body-touching middleware from being written on BaseHTTPMiddleware again.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"

# Attribute names that read or replace a request body, or reach the receive channel of a request.
_BODY_AND_RECEIVE_ATTRIBUTES = frozenset({"body", "stream", "form", "json", "_body", "_receive", "receive", "is_disconnected"})


def _defines_base_http_middleware(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
            if name == "BaseHTTPMiddleware":
                return True
    return False


def boundary_violations(source: str, filename: str = "<module>") -> list[str]:
    """What a BaseHTTPMiddleware module does that only a pure ASGI middleware may do; empty for any other module."""
    tree = ast.parse(source, filename)
    if not _defines_base_http_middleware(tree):
        return []

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _BODY_AND_RECEIVE_ATTRIBUTES:
            found.append(f"{filename}:{node.lineno} touches the request body or receive channel through `.{node.attr}`")
        elif isinstance(node, ast.Constant) and node.value == "websocket":
            found.append(f"{filename}:{node.lineno} handles a websocket scope")
        elif isinstance(node, ast.Name) and "websocket" in node.id.lower():
            found.append(f"{filename}:{node.lineno} handles a websocket through `{node.id}`")
    return found


def test_no_base_http_middleware_touches_a_body_the_receive_channel_or_a_websocket() -> None:
    violations: list[str] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        violations.extend(boundary_violations(path.read_text(encoding="utf-8"), str(path.relative_to(_APP_ROOT.parent))))

    assert not violations, "These middlewares must be pure ASGI (see app/middleware/asgi_body.py):\n  " + "\n  ".join(violations)


class TestTheGateJudgesTheShapesItExistsFor:
    def test_reading_the_body_in_a_base_http_middleware_is_flagged(self) -> None:
        source = """
from starlette.middleware.base import BaseHTTPMiddleware

class Decrypt(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        raw = await request.body()
        return await call_next(request)
"""
        assert len(boundary_violations(source)) == 1

    def test_replacing_the_cached_body_or_receive_is_flagged(self) -> None:
        source = """
from starlette.middleware import base

class Rewrite(base.BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request._body = b"x"
        request._receive = None
        return await call_next(request)
"""
        assert len(boundary_violations(source)) == 2

    def test_watching_for_a_disconnect_is_flagged(self) -> None:
        source = """
from starlette.middleware.base import BaseHTTPMiddleware

class Watch(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if await request.is_disconnected():
            return None
        return await call_next(request)
"""
        assert len(boundary_violations(source)) == 1

    def test_handling_a_websocket_in_a_base_http_middleware_is_flagged(self) -> None:
        source = """
from starlette.middleware.base import BaseHTTPMiddleware

class Guard(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.scope["type"] == "websocket":
            return None
        return await call_next(request)
"""
        assert len(boundary_violations(source)) == 1

    def test_a_base_http_middleware_that_only_reads_headers_is_clean(self) -> None:
        source = """
from starlette.middleware.base import BaseHTTPMiddleware

class Tag(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["x-seen"] = request.headers.get("x-id", "")
        return response
"""
        assert boundary_violations(source) == []

    @pytest.mark.parametrize("touch", ["await receive()", "scope['type'] == 'websocket'", "body = b''"])
    def test_a_pure_asgi_middleware_may_do_all_of_it(self, touch: str) -> None:
        source = f"""
class Rewrite:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        {touch}
        await self.app(scope, receive, send)
"""
        assert boundary_violations(source) == []
