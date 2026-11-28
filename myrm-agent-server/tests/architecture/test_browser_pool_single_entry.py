"""The global browser pool is built in one place, and no server module reaches into the harness singleton's state.

[INPUT]
- app/**/*.py: every call of ``get_global_browser_pool`` or ``GlobalBrowserPool``, with the function that makes it, and
  every mention of ``_global_pool`` (attribute, imported name or string)

[OUTPUT]
- Fails when the pool is built (``get_global_browser_pool(...)``, ``GlobalBrowserPool(...)``) anywhere but
  ``ensure_browser_pool`` in ``app/config/browser.py``, or when ``_global_pool`` is touched. It matches by name, so a
  builder renamed on import or reached through ``getattr`` is not seen.

[POS]
tests/architecture/test_browser_pool_single_entry.py - the harness singleton keeps the arguments of its first caller and
ignores every later one, so a second place that builds the pool decides the process's configuration whenever it runs
first (a pool without the proxy pool, the extension bridge or the cloud endpoint). Observers peek with
``peek_global_browser_pool``; closing goes through ``shutdown_global_browser_pool``, which also forgets the pool.
Writing ``_global_pool`` from outside drops a pool without closing it, or closes a pool and leaves it behind.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[2]
_APP = _SERVER_ROOT / "app"
_BUILDERS = frozenset({"get_global_browser_pool", "GlobalBrowserPool"})
_PRIVATE_STATE = "_global_pool"
_ENTRY = ("app/config/browser.py", "ensure_browser_pool")


class _Builds(ast.NodeVisitor):
    """``(line, builder, enclosing function)`` for each call of a pool builder."""

    def __init__(self) -> None:
        self.sites: list[tuple[int, str, str]] = []
        self._scope: list[str] = []

    def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _enter
    visit_AsyncFunctionDef = _enter

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name in _BUILDERS:
            self.sites.append((node.lineno, name, self._scope[-1] if self._scope else "<module>"))
        self.generic_visit(node)


def pool_builds(source: str) -> list[tuple[int, str, str]]:
    visitor = _Builds()
    visitor.visit(ast.parse(source))
    return visitor.sites


def private_state_mentions(source: str) -> Iterator[int]:
    """Line of each attribute, name, import or string literal that is ``_global_pool``."""
    for node in ast.walk(ast.parse(source)):
        mentioned = (
            (isinstance(node, ast.Attribute) and node.attr == _PRIVATE_STATE)
            or (isinstance(node, ast.Name) and node.id == _PRIVATE_STATE)
            or (isinstance(node, ast.alias) and node.name == _PRIVATE_STATE)
            or (isinstance(node, ast.Constant) and node.value == _PRIVATE_STATE)
        )
        if mentioned:
            yield getattr(node, "lineno", 0)


def _sources() -> Iterator[tuple[str, str]]:
    """``(path relative to the server root, source)`` of each Python file under ``app``."""
    for path in sorted(_APP.rglob("*.py")):
        yield path.relative_to(_SERVER_ROOT).as_posix(), path.read_text(encoding="utf-8")


def test_the_detector_sees_a_build_in_any_function_and_at_module_level() -> None:
    source = (
        "from pool import get_global_browser_pool as _alias\n"
        "import pool\n"
        "POOL = pool.GlobalBrowserPool()\n"
        "def outer():\n"
        "    async def inner():\n"
        "        return pool.get_global_browser_pool(max_browsers=1)\n"
    )

    assert pool_builds(source) == [(3, "GlobalBrowserPool", "<module>"), (6, "get_global_browser_pool", "inner")]


def test_the_detector_sees_every_form_of_touching_the_private_state() -> None:
    source = (
        "from singleton import _global_pool\n"
        "singleton._global_pool = None\n"
        'getattr(singleton, "_global_pool")\n'
        "peek = singleton.peek_global_browser_pool\n"
    )

    assert list(private_state_mentions(source)) == [1, 2, 3]


def test_the_one_entry_exists_so_the_scan_below_cannot_pass_over_nothing() -> None:
    path, function = _ENTRY
    sites = [(name, scope) for rel, source in _sources() if rel == path for _, name, scope in pool_builds(source)]

    assert sites == [("get_global_browser_pool", function)]


def test_the_pool_is_built_only_by_the_one_entry() -> None:
    offenders = [
        f"{rel}:{line}: {builder}(...) in {scope}()"
        for rel, source in _sources()
        for line, builder, scope in pool_builds(source)
        if (rel, scope) != _ENTRY
    ]

    assert not offenders, "build the pool with app.config.browser.ensure_browser_pool():\n" + "\n".join(offenders)


def test_no_server_module_touches_the_singletons_private_state() -> None:
    offenders = [f"{rel}:{line}" for rel, source in _sources() for line in private_state_mentions(source)]

    assert not offenders, "peek with peek_global_browser_pool(), close with shutdown_global_browser_pool():\n" + "\n".join(
        offenders
    )
