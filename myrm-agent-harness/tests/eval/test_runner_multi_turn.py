"""Tests for EvalRunner multi-turn execution."""

from __future__ import annotations

from typing import Any

import pytest

from myrm_agent_harness.eval.protocols import (
    AgentResponse,
    EvalCase,
    EvalTurnResult,
    MultiTurnEvalCase,
)
from myrm_agent_harness.eval.runner import EvalRunner
from tests.support.eval_mock_executor import MockExecutor


class TestEvalRunnerMultiTurn:
    """Tests for multi-turn eval execution."""

    @pytest.mark.asyncio
    async def test_multi_turn_basic(self) -> None:
        executor = MockExecutor(
            responses={
                "Hello": AgentResponse(answer="Hi", tools_called=[]),
                "Search X": AgentResponse(answer="Found X", tools_called=["web_search"]),
            }
        )
        runner = EvalRunner(executor)
        result = await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="Hello"),
                        EvalCase(message="Search X", expected_tools=["web_search"]),
                    ]
                )
            ]
        )

        assert result.total_cases == 2
        assert result.pass_count == 1
        assert result.skip_count == 1

    @pytest.mark.asyncio
    async def test_multi_turn_same_session(self) -> None:
        executor = MockExecutor()
        runner = EvalRunner(executor)
        await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="msg1"),
                        EvalCase(message="msg2"),
                    ]
                )
            ]
        )

        session_ids = [sid for _, sid in executor._call_log]
        assert session_ids[0] == session_ids[1]
        assert session_ids[0] is not None

    @pytest.mark.asyncio
    async def test_multi_turn_error_aborts_session(self) -> None:
        executor = MockExecutor(fail_on={"fail_msg"})
        runner = EvalRunner(executor)
        result = await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="fail_msg"),
                        EvalCase(message="should_not_run"),
                    ]
                )
            ]
        )

        assert result.total_cases == 1
        assert result.error_count == 1


class TestMultiTurnEdgeCases:
    """Edge cases and boundary conditions of multi-turn execution."""

    @pytest.mark.asyncio
    async def test_empty_multi_turn_list(self) -> None:
        executor = MockExecutor()
        runner = EvalRunner(executor)
        result = await runner.run_multi_turn([])

        assert result.total_cases == 0

    @pytest.mark.asyncio
    async def test_multi_turn_concurrent_multiple_sessions(self) -> None:
        """Multiple multi-turn sessions should run concurrently."""
        executor = MockExecutor(default_response=AgentResponse(answer="ok", tools_called=["t"]))
        runner = EvalRunner(executor, max_concurrency=3)
        cases = [
            MultiTurnEvalCase(
                turns=[
                    EvalCase(message=f"s{i}-t1", expected_tools=["t"]),
                    EvalCase(message=f"s{i}-t2", expected_tools=["t"]),
                ]
            )
            for i in range(3)
        ]
        result = await runner.run_multi_turn(cases)

        assert result.total_cases == 6
        assert result.pass_count == 6

    @pytest.mark.asyncio
    async def test_multi_turn_on_turn_fail_continue(self) -> None:
        """Default 'continue' runs all turns even after assertion failure."""
        executor = MockExecutor(
            responses={
                "t1": AgentResponse(answer="ok", tools_called=["t"]),
                "t2": AgentResponse(answer="bad", tools_called=["wrong"]),
                "t3": AgentResponse(answer="ok", tools_called=["t"]),
            }
        )
        runner = EvalRunner(executor)
        result = await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="t1", expected_tools=["t"]),
                        EvalCase(message="t2", expected_tools=["t"]),
                        EvalCase(message="t3", expected_tools=["t"]),
                    ],
                    on_turn_fail="continue",
                )
            ]
        )

        assert result.total_cases == 3
        assert result.pass_count == 2
        assert result.fail_count == 1

    @pytest.mark.asyncio
    async def test_multi_turn_on_turn_fail_skip_remaining(self) -> None:
        """'skip_remaining' emits skipped placeholders for unexecuted turns."""
        executor = MockExecutor(
            responses={
                "t1": AgentResponse(answer="ok", tools_called=["t"]),
                "t2": AgentResponse(answer="bad", tools_called=["wrong"]),
                "t3": AgentResponse(answer="ok", tools_called=["t"]),
            }
        )
        runner = EvalRunner(executor)
        result = await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="t1", expected_tools=["t"]),
                        EvalCase(message="t2", expected_tools=["t"]),
                        EvalCase(message="t3", expected_tools=["t"]),
                    ],
                    on_turn_fail="skip_remaining",
                )
            ]
        )

        assert result.total_cases == 3
        assert result.pass_count == 1
        assert result.fail_count == 1
        assert result.skip_count == 1
        assert result.turn_results[2].assertion_details == "skipped: prior turn assertion failed"

    @pytest.mark.asyncio
    async def test_multi_turn_on_turn_fail_abort(self) -> None:
        """'abort' stops immediately without emitting skipped turns."""
        executor = MockExecutor(
            responses={
                "t1": AgentResponse(answer="ok", tools_called=["t"]),
                "t2": AgentResponse(answer="bad", tools_called=["wrong"]),
                "t3": AgentResponse(answer="ok", tools_called=["t"]),
            }
        )
        runner = EvalRunner(executor)
        result = await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="t1", expected_tools=["t"]),
                        EvalCase(message="t2", expected_tools=["t"]),
                        EvalCase(message="t3", expected_tools=["t"]),
                    ],
                    on_turn_fail="abort",
                )
            ]
        )

        assert result.total_cases == 2
        assert result.pass_count == 1
        assert result.fail_count == 1

    @pytest.mark.asyncio
    async def test_multi_turn_on_turn_fail_first_turn_fails(self) -> None:
        """Abort on first turn failure skips all subsequent turns."""
        executor = MockExecutor(
            responses={
                "t1": AgentResponse(answer="bad", tools_called=["wrong"]),
                "t2": AgentResponse(answer="ok", tools_called=["t"]),
            }
        )
        runner = EvalRunner(executor)
        result = await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="t1", expected_tools=["t"]),
                        EvalCase(message="t2", expected_tools=["t"]),
                    ],
                    on_turn_fail="abort",
                )
            ]
        )

        assert result.total_cases == 1
        assert result.fail_count == 1

    @pytest.mark.asyncio
    async def test_multi_turn_error_aborts_regardless_of_strategy(self) -> None:
        """Execution errors always abort, even with on_turn_fail='continue'."""
        executor = MockExecutor(fail_on={"t2"})
        runner = EvalRunner(executor)
        result = await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="t1"),
                        EvalCase(message="t2"),
                        EvalCase(message="t3"),
                    ],
                    on_turn_fail="continue",
                )
            ]
        )

        assert result.total_cases == 2
        assert result.error_count == 1

    @pytest.mark.asyncio
    async def test_multi_turn_all_pass_with_abort_strategy(self) -> None:
        """All turns pass — abort strategy has no effect."""
        executor = MockExecutor(default_response=AgentResponse(answer="ok", tools_called=["t"]))
        runner = EvalRunner(executor)
        result = await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="t1", expected_tools=["t"]),
                        EvalCase(message="t2", expected_tools=["t"]),
                    ],
                    on_turn_fail="abort",
                )
            ]
        )

        assert result.total_cases == 2
        assert result.pass_count == 2

    @pytest.mark.asyncio
    async def test_multi_turn_skip_remaining_progress_callback(self) -> None:
        """Skipped turns should trigger the progress callback."""
        completed: list[EvalTurnResult] = []
        executor = MockExecutor(
            responses={
                "t1": AgentResponse(answer="bad", tools_called=["wrong"]),
                "t2": AgentResponse(answer="ok", tools_called=["t"]),
            }
        )
        runner = EvalRunner(executor, on_case_complete=completed.append)
        await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="t1", expected_tools=["t"]),
                        EvalCase(message="t2", expected_tools=["t"]),
                    ],
                    on_turn_fail="skip_remaining",
                )
            ]
        )

        assert len(completed) == 2
        assert completed[1].assertion_details == "skipped: prior turn assertion failed"

    @pytest.mark.asyncio
    async def test_multi_turn_middle_pass_last_fail(self) -> None:
        executor = MockExecutor(
            responses={
                "ok": AgentResponse(answer="ok", tools_called=["t"]),
                "bad": AgentResponse(answer="nope", tools_called=["wrong"]),
            }
        )
        runner = EvalRunner(executor)
        result = await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="ok", expected_tools=["t"]),
                        EvalCase(message="bad", expected_tools=["t"]),
                    ]
                )
            ]
        )

        assert result.pass_count == 1
        assert result.fail_count == 1

    @pytest.mark.asyncio
    async def test_multi_turn_abort_skips_all_sessions(self) -> None:
        """abort() should prevent multi-turn sessions from executing."""
        call_count = 0

        class CountingExecutor:
            async def create_session(self) -> str:
                return "s"

            def get_sandbox_executor(self, session_id: str | None = None) -> Any:
                return None

            async def execute(self, message: str, *, session_id: str | None = None) -> AgentResponse:
                nonlocal call_count
                call_count += 1
                return AgentResponse(answer="ok", tools_called=["t"])

        executor = CountingExecutor()
        runner = EvalRunner(executor, max_concurrency=1)
        runner.abort()

        result = await runner.run_multi_turn(
            [
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="t1", expected_tools=["t"]),
                        EvalCase(message="t2", expected_tools=["t"]),
                    ]
                ),
                MultiTurnEvalCase(
                    turns=[
                        EvalCase(message="t3", expected_tools=["t"]),
                    ]
                ),
            ]
        )

        assert result.total_cases == 0
        assert call_count == 0
