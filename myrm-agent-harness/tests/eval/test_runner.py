"""Tests for EvalRunner single-turn execution."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from myrm_agent_harness.eval.protocols import AgentResponse, EvalCase, EvalTurnResult, SandboxAssertion, StateAssertion
from myrm_agent_harness.eval.runner import EvalRunner
from myrm_agent_harness.toolkits.code_execution.executors.local import LocalExecutor
from tests.support.eval_mock_executor import MockExecutor


class TestEvalRunnerSingleTurn:
    """Tests for single-turn eval execution."""

    @pytest.mark.asyncio
    async def test_basic_pass(self) -> None:
        executor = MockExecutor(
            responses={
                "Search for Python": AgentResponse(
                    answer="Here are results",
                    tools_called=["web_search"],
                )
            }
        )
        runner = EvalRunner(executor)
        result = await runner.run(
            [
                EvalCase(
                    message="Search for Python",
                    expected_tools=["web_search"],
                )
            ]
        )

        assert result.total_cases == 1
        assert result.pass_count == 1
        assert result.all_passed is True
        assert result.total_ms > 0

    @pytest.mark.asyncio
    async def test_basic_fail(self) -> None:
        executor = MockExecutor(
            default_response=AgentResponse(
                answer="I don't know",
                tools_called=["chat"],
            )
        )
        runner = EvalRunner(executor)
        result = await runner.run(
            [
                EvalCase(
                    message="Search for Python",
                    expected_tools=["web_search"],
                )
            ]
        )

        assert result.fail_count == 1
        assert result.all_passed is False

    @pytest.mark.asyncio
    async def test_no_assertions_skip(self) -> None:
        executor = MockExecutor()
        runner = EvalRunner(executor)
        result = await runner.run([EvalCase(message="Hello")])

        assert result.skip_count == 1
        assert result.pass_count == 0
        assert result.fail_count == 0
        assert result.all_passed is True

    @pytest.mark.asyncio
    async def test_error_handling(self) -> None:
        executor = MockExecutor(fail_on={"bad message"})
        runner = EvalRunner(executor)
        result = await runner.run(
            [
                EvalCase(message="bad message", expected_tools=["web_search"]),
                EvalCase(message="good message"),
            ]
        )

        assert result.error_count == 1
        assert result.turn_results[0].error is not None
        assert result.turn_results[1].error is None

    @pytest.mark.asyncio
    async def test_a_failed_case_is_logged_with_its_stack_and_not_printed(
        self, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runner_logger = "myrm_agent_harness.eval.runner"
        runner = EvalRunner(MockExecutor(fail_on={"bad message"}))

        with caplog.at_level(logging.WARNING, logger=runner_logger):
            await runner.run([EvalCase(message="bad message")])

        assert [r.exc_info[0] for r in caplog.records if r.name == runner_logger and r.exc_info] == [RuntimeError]
        assert capsys.readouterr().err == ""

    @pytest.mark.asyncio
    async def test_concurrent_execution(self) -> None:
        call_times: list[float] = []

        class SlowExecutor:
            def __init__(self) -> None:
                self._counter = 0

            async def execute(self, message: str, *, session_id: str | None = None) -> AgentResponse:
                import time

                start = time.perf_counter()
                await asyncio.sleep(0.05)
                call_times.append(time.perf_counter() - start)
                return AgentResponse(answer="ok", tools_called=["tool"])

            async def create_session(self) -> str:
                self._counter += 1
                return f"s-{self._counter}"

            def get_sandbox_executor(self, session_id: str | None = None) -> None:
                return None

        runner = EvalRunner(SlowExecutor(), max_concurrency=5)
        cases = [EvalCase(message=f"msg-{i}", expected_tools=["tool"]) for i in range(5)]
        result = await runner.run(cases)

        assert result.pass_count == 5
        assert result.total_ms < 300

    @pytest.mark.asyncio
    async def test_progress_callback(self) -> None:
        completed: list[EvalTurnResult] = []
        executor = MockExecutor(default_response=AgentResponse(answer="ok", tools_called=["tool"]))
        runner = EvalRunner(executor, on_case_complete=completed.append)

        await runner.run(
            [
                EvalCase(message="a", expected_tools=["tool"]),
                EvalCase(message="b", expected_tools=["tool"]),
            ]
        )

        assert len(completed) == 2


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_cases_list(self) -> None:
        executor = MockExecutor()
        runner = EvalRunner(executor)
        result = await runner.run([])

        assert result.total_cases == 0
        assert result.all_passed is True
        assert result.pass_rate == 0.0

    @pytest.mark.asyncio
    async def test_max_concurrency_negative_clamped(self) -> None:
        """Negative max_concurrency should be clamped to 1."""
        executor = MockExecutor(default_response=AgentResponse(answer="ok", tools_called=["t"]))
        runner = EvalRunner(executor, max_concurrency=-5)
        result = await runner.run([EvalCase(message="test", expected_tools=["t"])])
        assert result.pass_count == 1

    @pytest.mark.asyncio
    async def test_max_concurrency_zero_clamped(self) -> None:
        executor = MockExecutor(default_response=AgentResponse(answer="ok", tools_called=["t"]))
        runner = EvalRunner(executor, max_concurrency=0)
        result = await runner.run([EvalCase(message="test", expected_tools=["t"])])
        assert result.pass_count == 1

    @pytest.mark.asyncio
    async def test_create_session_failure(self) -> None:
        """create_session raising should be captured as error."""

        class FailSessionExecutor:
            async def execute(self, message: str, *, session_id: str | None = None) -> AgentResponse:
                return AgentResponse(answer="ok")

            async def create_session(self) -> str:
                raise ConnectionError("DB unavailable")

            def get_sandbox_executor(self, session_id: str | None = None) -> None:
                return None

        runner = EvalRunner(FailSessionExecutor())
        result = await runner.run([EvalCase(message="test", expected_tools=["t"])])

        assert result.error_count == 1
        assert "DB unavailable" in (result.turn_results[0].error or "")

    @pytest.mark.asyncio
    async def test_mixed_pass_fail_skip_error(self) -> None:
        executor = MockExecutor(
            responses={
                "pass": AgentResponse(answer="ok", tools_called=["t"]),
                "fail": AgentResponse(answer="nope", tools_called=["wrong"]),
                "skip": AgentResponse(answer="hi"),
            },
            fail_on={"error"},
        )
        runner = EvalRunner(executor)
        result = await runner.run(
            [
                EvalCase(message="pass", expected_tools=["t"]),
                EvalCase(message="fail", expected_tools=["t"]),
                EvalCase(message="skip"),
                EvalCase(message="error", expected_tools=["t"]),
            ]
        )

        assert result.pass_count == 1
        assert result.fail_count == 1
        assert result.skip_count == 1
        assert result.error_count == 1
        assert result.total_cases == 4
        assert result.all_passed is False

    @pytest.mark.asyncio
    async def test_all_failed(self) -> None:
        executor = MockExecutor(default_response=AgentResponse(answer="nope", tools_called=["wrong"]))
        runner = EvalRunner(executor)
        result = await runner.run(
            [
                EvalCase(message="a", expected_tools=["t"]),
                EvalCase(message="b", expected_tools=["t"]),
            ]
        )

        assert result.fail_count == 2
        assert result.pass_rate == 0.0
        assert result.all_passed is False

    @pytest.mark.asyncio
    async def test_progress_callback_error_does_not_abort(self) -> None:
        """on_case_complete raising should not abort the run."""

        def bad_callback(result: EvalTurnResult) -> None:
            raise ValueError("callback error")

        executor = MockExecutor(default_response=AgentResponse(answer="ok", tools_called=["t"]))
        runner = EvalRunner(executor, on_case_complete=bad_callback)
        result = await runner.run(
            [
                EvalCase(message="a", expected_tools=["t"]),
                EvalCase(message="b", expected_tools=["t"]),
            ]
        )

        assert result.pass_count == 2

    @pytest.mark.asyncio
    async def test_require_all_integration(self) -> None:
        """require_all=True flows through runner correctly."""
        executor = MockExecutor(
            responses={
                "both": AgentResponse(answer="ok", tools_called=["web_search", "code_exec"]),
            }
        )
        runner = EvalRunner(executor)
        result = await runner.run(
            [
                EvalCase(
                    message="both",
                    expected_tools=["web_search", "code_exec"],
                    require_all=True,
                )
            ]
        )
        assert result.pass_count == 1


@pytest.mark.asyncio
async def test_run_abort_before_start() -> None:
    """Aborting before run() starts yields zero cases (semaphore-inner guard)."""
    executor = MockExecutor(default_response=AgentResponse(answer="ok", tools_called=["t"]))
    runner = EvalRunner(executor)
    runner.abort()
    result = await runner.run(
        [
            EvalCase(message="a", expected_tools=["t"]),
            EvalCase(message="b", expected_tools=["t"]),
        ]
    )
    assert result.total_cases == 0


@pytest.mark.asyncio
async def test_sandbox_and_state_assertions_execute(executor: LocalExecutor, tmp_path: Path) -> None:
    """Sandbox + state assertions run inside the runner and feed scores."""
    target = tmp_path / "hello.txt"
    target.write_text("hello world")

    class SandboxCapableExecutor(MockExecutor):
        def get_sandbox_executor(self, session_id: str | None = None) -> LocalExecutor:
            return executor

    runner = EvalRunner(SandboxCapableExecutor(default_response=AgentResponse(answer="ok", tools_called=["t"])))
    result = await runner.run(
        [
            EvalCase(
                message="test",
                expected_tools=["t"],
                sandbox_assertions=[SandboxAssertion(type="file_exists", target=str(target))],
                state_assertions=[StateAssertion(type="contains", expected="ok")],
            )
        ]
    )
    assert result.pass_count == 1
