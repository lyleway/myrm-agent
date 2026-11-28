"""Shutdown lets a manual compaction in flight finish before the components it needs are stopped.

A compaction is a summary a model was paid to write and the page is still waiting for: a process that exits at the
first SIGTERM loses both. The wait overlaps the Agent gateway's drain, so the two together stay inside the stop grace
the container is given, and it ends before any component the compaction depends on is stopped.
"""

from __future__ import annotations

import ast
import inspect
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.server import shutdown as shutdown_helpers
from app.server.lifespan.shutdown import _shutdown


def _lines_of_the_drain_in_shutdown() -> dict[str, int]:
    """The lines at which ``_shutdown`` starts the compaction drain, waits for the gateway drain, waits for the
    compaction drain and stops the components."""
    tree = ast.parse(inspect.getsource(_shutdown))
    lines: dict[str, int] = {}
    drain_task_name = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            started = node.value.args[0] if node.value.args else None
            if isinstance(started, ast.Call) and getattr(started.func, "id", "") == "safe_drain_compact_runs":
                lines["compaction drain starts"] = node.lineno
                drain_task_name = getattr(node.targets[0], "id", "")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == drain_task_name:
            lines["compaction drain awaited"] = node.lineno
        if isinstance(node.value, ast.Call):
            callee = node.value.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if name == "begin_drain":
                lines["gateway drain awaited"] = node.lineno
            if name == "gather":
                lines.setdefault("components stopped", node.lineno)
    return lines


def test_the_compaction_drain_runs_beside_the_gateway_drain_and_ends_before_the_components_stop() -> None:
    lines = _lines_of_the_drain_in_shutdown()

    assert set(lines) == {
        "compaction drain starts",
        "gateway drain awaited",
        "compaction drain awaited",
        "components stopped",
    }, lines
    assert (
        lines["compaction drain starts"]
        < lines["gateway drain awaited"]
        < lines["compaction drain awaited"]
        < lines["components stopped"]
    )


@pytest.mark.asyncio
async def test_the_drain_helper_waits_for_the_runs_in_flight() -> None:
    drain = AsyncMock()

    with patch("app.services.chat.compact.run.drain_compact_runs", new=drain):
        await shutdown_helpers.safe_drain_compact_runs()

    drain.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_a_drain_that_fails_is_logged_and_does_not_stop_the_shutdown(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch("app.services.chat.compact.run.drain_compact_runs", new=AsyncMock(side_effect=RuntimeError("registry gone"))),
        caplog.at_level(logging.WARNING, logger=shutdown_helpers.logger.name),
    ):
        await shutdown_helpers.safe_drain_compact_runs()

    assert "Compaction run drain failed: registry gone" in caplog.text
