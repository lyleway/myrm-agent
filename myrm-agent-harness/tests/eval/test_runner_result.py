"""Tests for the EvalRunner result: reporting views, dict schema and recorded timings."""

from __future__ import annotations

import pytest

from myrm_agent_harness.eval.protocols import AgentResponse, EvalCase
from myrm_agent_harness.eval.runner import EvalRunner
from tests.support.eval_mock_executor import MockExecutor


class TestEvalResult:
    """Tests for EvalResult reporting."""

    @pytest.mark.asyncio
    async def test_to_dict(self) -> None:
        executor = MockExecutor(default_response=AgentResponse(answer="ok", tools_called=["t"]))
        runner = EvalRunner(executor)
        result = await runner.run([EvalCase(message="test", expected_tools=["t"])])

        d = result.to_dict()
        assert d["total_cases"] == 1
        assert d["pass_count"] == 1
        assert d["all_passed"] is True
        assert isinstance(d["turns"], list)

    @pytest.mark.asyncio
    async def test_to_json(self) -> None:
        import json

        executor = MockExecutor()
        runner = EvalRunner(executor)
        result = await runner.run([EvalCase(message="test")])

        j = result.to_json()
        parsed = json.loads(j)
        assert "total_cases" in parsed

    @pytest.mark.asyncio
    async def test_summary(self) -> None:
        executor = MockExecutor(default_response=AgentResponse(answer="ok", tools_called=["t"]))
        runner = EvalRunner(executor)
        result = await runner.run([EvalCase(message="test", expected_tools=["t"])])

        s = result.summary()
        assert "1/1 passed" in s
        assert "100%" in s

    @pytest.mark.asyncio
    async def test_pass_rate_no_assertions(self) -> None:
        executor = MockExecutor()
        runner = EvalRunner(executor)
        result = await runner.run([EvalCase(message="test")])

        assert result.pass_rate == 0.0


class TestTurnResultFields:
    """Fields of a turn result: the dict schema and the recorded timings."""

    @pytest.mark.asyncio
    async def test_to_dict_all_fields(self) -> None:
        """Verify to_dict contains all expected fields."""
        executor = MockExecutor(default_response=AgentResponse(answer="ok", tools_called=["t"]))
        runner = EvalRunner(executor)
        result = await runner.run([EvalCase(message="test", expected_tools=["t"])])

        d = result.to_dict()
        required_keys = {
            "total_cases",
            "pass_count",
            "fail_count",
            "error_count",
            "skip_count",
            "pass_rate",
            "all_passed",
            "total_ms",
            "total_tokens",
            "total_cost",
            "failure_analysis",
            "ablation_recommendations",
            "signature_clusters",
            "turns",
        }
        assert required_keys == set(d.keys())

        turns = d["turns"]
        assert isinstance(turns, list)
        turn = turns[0]
        turn_keys = {
            "message",
            "expected_tools",
            "sandbox_assertions",
            "state_assertions",
            "semantic_assertions",
            "retrieval_assertions",
            "compaction_assertions",
            "tools_called",
            "tool_call_details",
            "retrieved_hits",
            "limit_reached",
            "blocked_count",
            "assertion_passed",
            "assertion_details",
            "post_episode_passed",
            "canary_verified",
            "contamination_audit",
            "scores",
            "total_ms",
            "token_usage",
            "cost",
            "error",
        }
        assert turn_keys == set(turn.keys())

    @pytest.mark.asyncio
    async def test_timings_recorded(self) -> None:
        executor = MockExecutor()
        runner = EvalRunner(executor)
        result = await runner.run([EvalCase(message="test")])

        assert result.total_ms > 0
        assert result.turn_results[0].timings.total_ms > 0

    @pytest.mark.asyncio
    async def test_extra_timings_propagated(self) -> None:
        """AgentResponse.extra_timings should appear in EvalTimings.extra."""
        executor = MockExecutor(
            default_response=AgentResponse(
                answer="ok",
                tools_called=["t"],
                extra_timings={"llm_first_token_ms": 150.0},
            )
        )
        runner = EvalRunner(executor)
        result = await runner.run([EvalCase(message="test", expected_tools=["t"])])

        assert result.turn_results[0].timings.extra["llm_first_token_ms"] == 150.0
