"""What an ACP agent's turn reports: the reason it gave for stopping, and the text around its tool calls.

The scripted agent ends a prompt with the reason it is told (``/stop <reason>``) and, on ``/story``, speaks before and
after a tool call as a real agent does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.types import RuntimeEventType, StopReason

from ._fakes import make_pool, run_turn

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the scripted ACP agent is exercised on POSIX only")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


class TestTheReasonTheAgentStopped:
    @pytest.mark.parametrize("reason", list(StopReason))
    async def test_the_end_of_the_turn_carries_the_reason_the_agent_gave(
        self, workspace: Path, reason: StopReason
    ) -> None:
        pool = make_pool()
        try:
            _, events = await run_turn(pool, [], workspace=workspace, prompt=f"/stop {reason.value}")
        finally:
            await pool.close_all()

        (done,) = [event for event in events if event.type is RuntimeEventType.DONE]
        assert StopReason.of(done.data["stop_reason"]) is reason

    async def test_a_turn_that_simply_finished_ends_with_end_turn(self, workspace: Path) -> None:
        pool = make_pool()
        try:
            _, events = await run_turn(pool, [], workspace=workspace, prompt="hello")
        finally:
            await pool.close_all()

        (done,) = [event for event in events if event.type is RuntimeEventType.DONE]
        assert StopReason.of(done.data["stop_reason"]) is StopReason.END_TURN


class TestTextAroundAToolCall:
    async def test_the_message_after_a_tool_call_is_set_apart_by_a_blank_line(self, workspace: Path) -> None:
        pool = make_pool()
        try:
            text, events = await run_turn(pool, [], workspace=workspace, prompt="/story")
        finally:
            await pool.close_all()

        assert text == "before\n\nafter"
        types = [event.type for event in events]
        assert types.index(RuntimeEventType.TOOL_START) < types.index(RuntimeEventType.TOOL_RESULT)

    async def test_a_turn_does_not_begin_with_what_the_turn_before_it_left_over(self, workspace: Path) -> None:
        pool = make_pool()
        try:
            await run_turn(pool, [], workspace=workspace, prompt="/story")
            text, _ = await run_turn(pool, [], workspace=workspace, prompt="hello")
        finally:
            await pool.close_all()

        assert text == "[echo] hello"

    async def test_text_with_no_tool_call_between_is_left_as_the_agent_wrote_it(self, workspace: Path) -> None:
        pool = make_pool()
        try:
            text, _ = await run_turn(pool, [], workspace=workspace, prompt="hello")
        finally:
            await pool.close_all()

        assert text == "[echo] hello"
