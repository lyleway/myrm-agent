"""How the delegate tool handles the stream of a delegated turn: stopping, turn limits, early endings, step text."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from myrm_agent_harness.toolkits.acp.acp_agent_tools import _run_turn_and_collect
from myrm_agent_harness.toolkits.acp.delegate_report import STOP_NOTES
from myrm_agent_harness.toolkits.acp.types import (
    AcpError,
    AcpErrorCode,
    ContinuityStatus,
    RuntimeConfig,
    RuntimeEventType,
    StopReason,
    create_event,
)

_TOOLS = "myrm_agent_harness.toolkits.acp.acp_agent_tools"
_WORKSPACE = "/tmp/ws"


def _pool(command: str, *, max_turns: int = 25, name: str = "agent") -> MagicMock:
    config = RuntimeConfig(backend_type="cli", command=command, max_turns=max_turns)
    pool = MagicMock()
    pool.available_backends = [name]
    pool.get_config = MagicMock(return_value=config)
    pool.cancel = AsyncMock()
    return pool


def _error(session_id: str, code: AcpErrorCode, message: str = "stopped"):
    return create_event(RuntimeEventType.ERROR, session_id, error=AcpError(code=code, message=message))


class TestNativeTurnLimit:
    @pytest.mark.asyncio
    async def test_tool_calls_of_a_cli_that_limits_itself_are_not_counted_against_the_limit(self) -> None:
        """One Claude turn can run several tool calls, so counting them would cut it off before its own limit."""
        pool = _pool("claude", max_turns=2)

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            for tool in ("Read", "Grep", "Bash"):
                yield create_event(RuntimeEventType.TOOL_START, session_id, tool_name=tool)
            yield create_event(RuntimeEventType.TEXT_DELTA, session_id, content="finished")
            yield create_event(RuntimeEventType.DONE, session_id, stop_reason="end_turn")

        pool.run_turn = stream
        result, meta = await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert result == "finished"
        assert meta["tool_calls"] == 3
        pool.cancel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_cli_own_turn_limit_ends_the_delegation_with_the_work_it_did(self) -> None:
        pool = _pool("claude", max_turns=7)

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            yield create_event(RuntimeEventType.TEXT_DELTA, session_id, content="partial findings")
            yield create_event(RuntimeEventType.DONE, session_id, stop_reason=StopReason.MAX_TURN_REQUESTS)

        pool.run_turn = stream
        result, meta = await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert result == f"partial findings\n\n{STOP_NOTES[StopReason.MAX_TURN_REQUESTS]}"
        assert meta["errors"] == 0

    @pytest.mark.asyncio
    async def test_a_cli_without_a_limit_of_its_own_is_still_counted(self) -> None:
        pool = _pool("gemini", max_turns=2)

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            for tool in ("a", "b", "c"):
                yield create_event(RuntimeEventType.TOOL_START, session_id, tool_name=tool)

        pool.run_turn = stream
        result, _ = await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert result.endswith("[stopped — max turns limit reached (2/2)]")
        pool.cancel.assert_awaited()

    @pytest.mark.asyncio
    async def test_an_acp_backend_is_counted_even_when_its_command_is_claude(self) -> None:
        """The native limit is a property of the CLI backend, not of the command name an ACP adapter shares."""
        config = RuntimeConfig(backend_type="acp", command="claude", max_turns=2)
        pool = _pool("claude")
        pool.get_config = MagicMock(return_value=config)

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            for tool in ("a", "b", "c"):
                yield create_event(RuntimeEventType.TOOL_START, session_id, tool_name=tool)

        pool.run_turn = stream
        result, _ = await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert "max turns limit reached" in result


class TestAnAgentThatEndsItsTurnEarly:
    @staticmethod
    def _ending_with(reason: str | None):
        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            yield create_event(RuntimeEventType.TEXT_DELTA, session_id, content="the first half")
            yield create_event(RuntimeEventType.DONE, session_id, stop_reason=reason)

        return stream

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("reason", "note"),
        [
            (StopReason.MAX_TOKENS, "[stopped — the agent reached its output limit; the reply above is incomplete]"),
            (StopReason.MAX_TURN_REQUESTS, "[stopped — the agent reached its own turn limit]"),
            (StopReason.REFUSAL, "[stopped — the agent declined to continue]"),
        ],
    )
    async def test_the_result_says_why_it_is_incomplete(self, reason: StopReason, note: str) -> None:
        """The calling model reads the result; without the note it would take half a reply for the whole one."""
        pool = _pool("gemini")
        pool.run_turn = self._ending_with(reason.value)

        result, meta = await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert result == f"the first half\n\n{note}"
        assert meta["errors"] == 0
        pool.cancel.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reason", [StopReason.END_TURN.value, StopReason.CANCELLED.value, "something_new", None])
    async def test_a_turn_that_finished_adds_nothing(self, reason: str | None) -> None:
        pool = _pool("gemini")
        pool.run_turn = self._ending_with(reason)

        result, _ = await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert result == "the first half"

    @pytest.mark.asyncio
    async def test_the_user_is_told_with_a_finished_step_without_text(self) -> None:
        pool = _pool("gemini")
        pool.run_turn = self._ending_with(StopReason.MAX_TOKENS.value)
        sink = AsyncMock()

        with patch(f"{_TOOLS}.get_tool_progress_sink", return_value=sink):
            await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert [call.args[0] for call in sink.emit.await_args_list] == [
            {
                "type": "tasks_steps",
                "step_key": "delegation_agent_max_tokens",
                "tool_name": "delegate:agent",
                "status": "completed",
                "data": [],
            }
        ]


class TestStopping:
    @pytest.mark.asyncio
    async def test_a_cancelled_delegation_event_is_a_stop_not_a_failure(self) -> None:
        pool = _pool("claude")

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            yield create_event(RuntimeEventType.TEXT_DELTA, session_id, content="so far")
            yield _error(session_id, AcpErrorCode.CANCELLED)

        pool.run_turn = stream
        result, meta = await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert result == "so far\n\n[cancelled — user aborted delegation]"
        assert meta["errors"] == 0

    @pytest.mark.asyncio
    async def test_a_tripped_token_stops_the_delegate_while_its_stream_is_silent(self) -> None:
        """Stop pressed while the CLI says nothing: the pool is told at once, not at the next event."""
        pool = _pool("claude")
        released = asyncio.Event()
        pool.cancel = AsyncMock(side_effect=lambda *_: released.set())

        class Token:
            request_id = "r1"
            is_cancelled = False

        token = Token()

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            await released.wait()
            yield _error(session_id, AcpErrorCode.CANCELLED)

        pool.run_turn = stream

        async def press_stop() -> None:
            await asyncio.sleep(0.1)
            token.is_cancelled = True

        with patch(f"{_TOOLS}.get_cancel_token", return_value=token):
            stopper = asyncio.create_task(press_stop())
            async with asyncio.timeout(10):
                result, _ = await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")
            await stopper

        assert result.endswith("[cancelled — user aborted delegation]")
        pool.cancel.assert_awaited_with("agent", pool.cancel.await_args.args[1])

    @pytest.mark.asyncio
    async def test_leaving_the_stream_early_closes_it(self) -> None:
        """The runtime stops its CLI when its stream is closed, so the tool must close the stream it abandons."""
        pool = _pool("codex", max_turns=1)
        closed = asyncio.Event()

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            try:
                yield create_event(RuntimeEventType.TOOL_START, session_id, tool_name="a")
                yield create_event(RuntimeEventType.TEXT_DELTA, session_id, content="never read")
            finally:
                closed.set()

        pool.run_turn = stream
        await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert closed.is_set()


class TestProgressSteps:
    @staticmethod
    def _emitted(sink: AsyncMock) -> list[dict[str, object]]:
        return [call.args[0] for call in sink.emit.await_args_list]

    @pytest.mark.asyncio
    async def test_a_tool_step_says_what_the_call_works_on(self) -> None:
        pool = _pool("claude")
        sink = AsyncMock()

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            yield create_event(RuntimeEventType.TOOL_START, session_id, tool_name="Bash", summary="npm test")
            yield create_event(RuntimeEventType.TOOL_START, session_id, tool_name="Read", summary="")
            yield create_event(RuntimeEventType.DONE, session_id, stop_reason="end_turn")

        pool.run_turn = stream
        with patch(f"{_TOOLS}.get_tool_progress_sink", return_value=sink):
            await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        steps = self._emitted(sink)
        assert [step["data"] for step in steps] == [[{"text": "agent: Bash — npm test"}], [{"text": "agent: Read"}]]
        assert {step["step_key"] for step in steps} == {"delegation_agent_tool"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", list(ContinuityStatus))
    async def test_a_continuity_notice_is_a_finished_step_without_text(self, status: ContinuityStatus) -> None:
        """The UI words these itself; the harness only says which one happened."""
        pool = _pool("claude")
        sink = AsyncMock()

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            yield create_event(RuntimeEventType.STATUS_UPDATE, session_id, status=status)
            yield create_event(RuntimeEventType.DONE, session_id, stop_reason="end_turn")

        pool.run_turn = stream
        with patch(f"{_TOOLS}.get_tool_progress_sink", return_value=sink):
            await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert self._emitted(sink) == [
            {
                "type": "tasks_steps",
                "step_key": f"delegation_agent_{status.value}",
                "tool_name": "delegate:agent",
                "status": "completed",
                "data": [],
            }
        ]

    @pytest.mark.asyncio
    async def test_other_statuses_stay_running_text_steps(self) -> None:
        pool = _pool("claude")
        sink = AsyncMock()

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            yield create_event(RuntimeEventType.STATUS_UPDATE, session_id, status="starting")
            yield create_event(RuntimeEventType.DONE, session_id, stop_reason="end_turn")

        pool.run_turn = stream
        with patch(f"{_TOOLS}.get_tool_progress_sink", return_value=sink):
            await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert self._emitted(sink) == [
            {
                "type": "tasks_steps",
                "step_key": "delegation_agent_status",
                "tool_name": "delegate:agent",
                "data": [{"text": "agent: starting"}],
            }
        ]


class TestErrors:
    @pytest.mark.asyncio
    async def test_a_real_error_still_fails_the_delegation_with_its_retry_hint(self) -> None:
        pool = _pool("claude")

        async def stream(name, task, session_id, *, workspace, mode="persistent", permissions=None):
            yield create_event(
                RuntimeEventType.ERROR,
                session_id,
                error=AcpError(code=AcpErrorCode.RATE_LIMITED, message="slow down", retryable=True),
            )

        pool.run_turn = stream
        with pytest.raises(RuntimeError, match="slow down") as raised:
            await _run_turn_and_collect(pool, "agent", "task", workspace=_WORKSPACE, mode="persistent")

        assert raised.value.retryable is True  # type: ignore[attr-defined]
