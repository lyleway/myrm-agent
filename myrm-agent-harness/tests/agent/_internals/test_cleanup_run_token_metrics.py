"""A run's end reports each model's token counts to the metrics registry, and only counts that are integers.

``cleanup_run`` is where a run's per-model usage becomes counter increments. That usage dict also carries floats
(``cost_usd``), so the counts are checked before they reach a registry that expects integers.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, call, patch

from myrm_agent_harness.agent._internals.run_lifecycle import cleanup_run
from myrm_agent_harness.agent.types import AgentRunStatistics

_LIFECYCLE = "myrm_agent_harness.agent._internals.run_lifecycle"
_REGISTRY = "myrm_agent_harness.observability.metrics.registry.metrics_registry"


def _end_run(model_usage: dict[str, dict[str, object]], *, metrics_enabled: bool = True) -> MagicMock:
    """End a run whose statistics carry ``model_usage``; returns the registry that was asked to record it."""
    registry = MagicMock(enabled=metrics_enabled)
    with (
        patch(_REGISTRY, registry),
        patch(f"{_LIFECYCLE}.collect_tracker_stats"),
        patch(f"{_LIFECYCLE}.reset_token_tracker"),
        patch(f"{_LIFECYCLE}.clear_pending_explicit_cache_snapshot"),
    ):
        cleanup_run(
            AgentRunStatistics(model_usage=model_usage),
            time.time(),
            cancel_token=None,
            steering_token=None,
            cancel_all_fn=lambda include_detached=True: 0,
        )
    return registry


def test_every_models_token_counts_are_recorded() -> None:
    registry = _end_run(
        {
            "model-a": {"prompt_tokens": 120, "completion_tokens": 30, "cost_usd": 0.0042},
            "model-b": {"prompt_tokens": 7, "completion_tokens": 0, "cost_usd": 0.0},
        }
    )

    assert registry.record_tokens.call_args_list == [
        call(agent_id="base_agent", model="model-a", prompt_tokens=120, completion_tokens=30),
        call(agent_id="base_agent", model="model-b", prompt_tokens=7, completion_tokens=0),
    ]


def test_a_models_counts_that_are_not_integers_are_not_recorded() -> None:
    registry = _end_run(
        {
            "model-a": {"prompt_tokens": "120", "completion_tokens": 30},
            "model-b": {"prompt_tokens": 7, "completion_tokens": 2.5},
            "model-c": {"prompt_tokens": 5, "completion_tokens": 1},
        }
    )

    assert registry.record_tokens.call_args_list == [
        call(agent_id="base_agent", model="model-c", prompt_tokens=5, completion_tokens=1),
    ]


def test_nothing_is_recorded_while_metrics_are_disabled() -> None:
    registry = _end_run({"model-a": {"prompt_tokens": 1, "completion_tokens": 1}}, metrics_enabled=False)

    registry.record_tokens.assert_not_called()
