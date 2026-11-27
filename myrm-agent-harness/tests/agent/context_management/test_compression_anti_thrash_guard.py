"""Unit tests for compression_anti_thrash_guard."""

from __future__ import annotations

from uuid import uuid4

import pytest

from myrm_agent_harness.agent.context_management.strategies.compression.compression_anti_thrash_guard import (
    ANTI_THRASHING_STREAK_LIMIT,
    record_compression_effectiveness,
    should_block_automatic_compression,
)
from myrm_agent_harness.agent.context_management.tracking.task_metrics import (
    create_task_metrics,
    get_task_metrics,
)


def test_should_block_when_streak_high_and_below_safety_net() -> None:
    chat_id = "guard-block-test"
    metrics = create_task_metrics(chat_id)
    metrics.compression_ineffective_streak = ANTI_THRASHING_STREAK_LIMIT

    assert should_block_automatic_compression(chat_id, total_tokens=50_000, max_context_tokens=128_000)


def test_should_not_block_when_above_safety_net() -> None:
    chat_id = "guard-override-test"
    metrics = create_task_metrics(chat_id)
    metrics.compression_ineffective_streak = ANTI_THRASHING_STREAK_LIMIT

    assert not should_block_automatic_compression(
        chat_id,
        total_tokens=int(128_000 * 0.91),
        max_context_tokens=128_000,
    )


def test_should_not_block_below_the_streak_limit() -> None:
    chat_id = "guard-below-limit"
    create_task_metrics(chat_id).compression_ineffective_streak = ANTI_THRASHING_STREAK_LIMIT - 1

    assert not should_block_automatic_compression(chat_id, total_tokens=50_000, max_context_tokens=128_000)


def test_a_turn_without_a_chat_is_never_blocked() -> None:
    assert not should_block_automatic_compression(None, total_tokens=50_000, max_context_tokens=128_000)


def test_record_resets_streak_on_effective_savings() -> None:
    chat_id = "guard-record-reset"
    metrics = create_task_metrics(chat_id)
    metrics.compression_ineffective_streak = 2

    record_compression_effectiveness(chat_id, original_tokens=10_000, tokens_saved=2_000)

    updated = get_task_metrics(chat_id)
    assert updated is not None
    assert updated.compression_ineffective_streak == 0


def test_record_increments_streak_on_ineffective_savings() -> None:
    chat_id = "guard-record-inc"
    metrics = create_task_metrics(chat_id)
    metrics.compression_ineffective_streak = 1

    record_compression_effectiveness(chat_id, original_tokens=10_000, tokens_saved=50)

    updated = get_task_metrics(chat_id)
    assert updated is not None
    assert updated.compression_ineffective_streak == 2


def test_record_creates_metrics_when_missing() -> None:
    chat_id = "guard-record-missing-metrics"

    record_compression_effectiveness(chat_id, original_tokens=10_000, tokens_saved=50)

    updated = get_task_metrics(chat_id)
    assert updated is not None
    assert updated.compression_ineffective_streak == 1


def test_record_ignores_a_compaction_that_started_from_no_tokens() -> None:
    chat_id = f"guard-record-no-tokens-{uuid4().hex}"

    record_compression_effectiveness(chat_id, original_tokens=0, tokens_saved=0)

    assert get_task_metrics(chat_id) is None


_INEFFECTIVE = 50
_EFFECTIVE = 2_000


@pytest.mark.parametrize(
    ("streak_before", "ceiling", "tokens_saved", "streak_after"),
    [
        pytest.param(
            0,
            ANTI_THRASHING_STREAK_LIMIT,
            _INEFFECTIVE,
            1,
            id="an-ineffective-attempt-extends-a-streak-below-the-ceiling",
        ),
        pytest.param(
            ANTI_THRASHING_STREAK_LIMIT - 1,
            ANTI_THRASHING_STREAK_LIMIT,
            _INEFFECTIVE,
            ANTI_THRASHING_STREAK_LIMIT,
            id="an-ineffective-attempt-can-reach-the-ceiling",
        ),
        pytest.param(
            ANTI_THRASHING_STREAK_LIMIT,
            ANTI_THRASHING_STREAK_LIMIT,
            _INEFFECTIVE,
            ANTI_THRASHING_STREAK_LIMIT,
            id="an-ineffective-attempt-does-not-pass-the-ceiling",
        ),
        pytest.param(
            5, ANTI_THRASHING_STREAK_LIMIT, _INEFFECTIVE, 5, id="a-streak-already-above-the-ceiling-is-left-alone"
        ),
        pytest.param(
            ANTI_THRASHING_STREAK_LIMIT,
            ANTI_THRASHING_STREAK_LIMIT,
            _EFFECTIVE,
            0,
            id="an-effective-attempt-still-clears-it",
        ),
        pytest.param(
            ANTI_THRASHING_STREAK_LIMIT,
            None,
            _INEFFECTIVE,
            ANTI_THRASHING_STREAK_LIMIT + 1,
            id="without-a-ceiling-the-streak-keeps-growing",
        ),
    ],
)
def test_record_streak_ceiling(streak_before: int, ceiling: int | None, tokens_saved: int, streak_after: int) -> None:
    chat_id = f"guard-record-ceiling-{uuid4().hex}"
    create_task_metrics(chat_id).compression_ineffective_streak = streak_before

    record_compression_effectiveness(chat_id, original_tokens=10_000, tokens_saved=tokens_saved, streak_ceiling=ceiling)

    updated = get_task_metrics(chat_id)
    assert updated is not None
    assert updated.compression_ineffective_streak == streak_after
