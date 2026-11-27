"""What the real claude binary says when its model API refuses a request, and what ``CliRuntime`` makes of it.

A local stub API answers every request with one canned failure, so nothing here needs a login, a network or a model
(and no model is billed). These guard against drift in the failure frames the classifier reads: Claude's
``authentication_failed`` category and HTTP status. The CLI runs through the production runtime with the production
argv and environment rules. Most tests turn the CLI's own retries off so a failure comes back at once; the one about a
rejected key leaves them at their default, which is what a user's CLI does (it retries a 401 for minutes unless the
runtime stops it).
Run with ``-m integration``; a CLI that is not installed skips its tests.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import cli_launch_args
from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime
from myrm_agent_harness.toolkits.acp.types import (
    AcpError,
    AcpErrorCode,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
)

pytestmark = pytest.mark.integration

_CLAUDE = shutil.which("claude")

needs_claude = pytest.mark.skipif(_CLAUDE is None, reason="claude CLI not installed")

_AUTH_BODY = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
_FORBIDDEN_BODY = {"type": "error", "error": {"type": "permission_error", "message": "Request not allowed"}}
_RATE_LIMIT_BODY = {"type": "error", "error": {"type": "rate_limit_error", "message": "rate limit reached"}}

ServeFailure = Callable[[int, dict[str, object]], str]


@pytest.fixture
def serve_failure() -> Iterator[ServeFailure]:
    """Start a local API answering every request with ``status`` and ``body``; yields the function that starts one."""
    servers: list[ThreadingHTTPServer] = []

    def serve(status: int, body: dict[str, object]) -> str:
        payload = json.dumps(body).encode()

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                self._answer()

            def do_POST(self) -> None:
                self._answer()

            def log_message(self, format: str, *args: object) -> None:
                return None

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


def _claude(
    tmp_path: Path, base_url: str, *, api_key: str | None = "sk-ant-api03-dummy", retries: str | None = "0"
) -> CliRuntime:
    """A claude pointed at the stub; ``retries`` is its ``CLAUDE_CODE_MAX_RETRIES`` (None leaves the CLI's default)."""
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "ANTHROPIC_BASE_URL": base_url,
        "API_TIMEOUT_MS": "8000",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    if retries is not None:
        env["CLAUDE_CODE_MAX_RETRIES"] = retries
    if api_key is not None:
        env["ANTHROPIC_API_KEY"] = api_key
    config = RuntimeConfig(
        backend_type="cli",
        command=str(_CLAUDE),
        args=cli_launch_args("claude"),
        env=env,
        permission_mode="safe",
        auth_mode="api_key",
        timeout_seconds=60,
    )
    return CliRuntime("claude", config, workspace=str(tmp_path))


async def _errors_of(runtime: CliRuntime) -> tuple[list[RuntimeEvent], list[AcpError]]:
    events = [event async for event in runtime.run_turn("hi", "s1")]
    errors = [e.data["error"] for e in events if e.type is RuntimeEventType.ERROR]
    assert all(isinstance(error, AcpError) for error in errors)
    return events, errors


@needs_claude
@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, _AUTH_BODY, AcpErrorCode.AUTH_FAILED),
        # Claude files a 403 under the same category as a rejected key, but it is a blocked region or a policy.
        (403, _FORBIDDEN_BODY, AcpErrorCode.UNKNOWN),
        (429, _RATE_LIMIT_BODY, AcpErrorCode.RATE_LIMITED),
    ],
    ids=["unauthorized", "forbidden", "rate-limited"],
)
async def test_claude_names_what_its_api_refused(
    serve_failure: ServeFailure, tmp_path: Path, status: int, body: dict[str, object], code: AcpErrorCode
) -> None:
    _, errors = await _errors_of(_claude(tmp_path, serve_failure(status, body)))

    assert [error.code for error in errors] == [code]
    assert errors[0].message.strip()


@needs_claude
async def test_claude_without_any_credentials_is_an_auth_failure(serve_failure: ServeFailure, tmp_path: Path) -> None:
    # No API key and an empty config dir; the base URL points at the stub so a login found elsewhere can only reach it.
    _, errors = await _errors_of(_claude(tmp_path, serve_failure(401, _AUTH_BODY), api_key=None))

    assert [error.code for error in errors] == [AcpErrorCode.AUTH_FAILED]


# What a user waits for when the CLI is left to retry a rejected key by itself (measured: claude 183 s).
_REJECTED_KEY_SECONDS = 40


@needs_claude
async def test_claude_rejected_key_is_reported_within_seconds_although_the_cli_retries_for_minutes(
    serve_failure: ServeFailure, tmp_path: Path
) -> None:
    started = time.monotonic()

    _, errors = await _errors_of(_claude(tmp_path, serve_failure(401, _AUTH_BODY), retries=None))

    assert [error.code for error in errors] == [AcpErrorCode.AUTH_FAILED]
    assert "401" in errors[0].message
    assert time.monotonic() - started < _REJECTED_KEY_SECONDS
