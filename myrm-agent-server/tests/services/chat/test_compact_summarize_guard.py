"""The progress-aware guard around the compaction's model call.

A model that keeps producing output is left alone however long it takes; one that goes silent, or that runs past the
total ceiling, is cut off and stopped, so a compaction never hangs the chat that asked for it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from myrm_agent_harness.agent.context_management.infra.schemas import StructuredSummary
from myrm_agent_harness.agent.context_management.strategies.summary import progress_timeout, summarizer
from myrm_agent_harness.agent.context_management.strategies.summary.progress_timeout import (
    InactivityTimeoutError,
    ProgressClock,
    TotalCeilingTimeoutError,
)

from app.services.chat.compact import summarize_guard
from app.services.chat.compact.summarize_guard import guarded_compact_summarize

_GUARD_LOGGER = "app.services.chat.compact.summarize_guard"
_MESSAGES: list[BaseMessage] = [HumanMessage(content="hello")]
_KEPT: list[BaseMessage] = [HumanMessage(content="kept")]
_SUMMARY = StructuredSummary(user_goal="ship the release")
_Answer = tuple[list[BaseMessage], StructuredSummary]


class _Clock:
    """Stands in for the progress clock: the test says how long the model has been silent, and each look is counted."""

    def __init__(self, silent_for: float) -> None:
        self.silent_for = silent_for
        self.looks = 0
        self.looked_three_times = asyncio.Event()

    @property
    def seconds_since_last_touch(self) -> float:
        self.looks += 1
        if self.looks >= 3:
            self.looked_three_times.set()
        return self.silent_for


class _StuckModel:
    """A model call that never answers and records that it was stopped."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = False

    async def __call__(self, **_kwargs: object) -> _Answer:
        self.started.set()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.stopped = True
            raise


def _model_answers(monkeypatch: pytest.MonkeyPatch, answer: Callable[..., Coroutine[object, object, _Answer]]) -> None:
    monkeypatch.setattr(summarizer, "generate_structured_summary", answer)


def _stuck_model(monkeypatch: pytest.MonkeyPatch) -> _StuckModel:
    model = _StuckModel()
    _model_answers(monkeypatch, model)
    return model


def _silent_for(monkeypatch: pytest.MonkeyPatch, seconds: float) -> _Clock:
    clock = _Clock(seconds)
    monkeypatch.setattr(progress_timeout, "ProgressClock", lambda: clock)
    return clock


@pytest.fixture
def fast_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    """The watchdog looks every 10 ms instead of every 10 s; what the clock reports decides, not the wall clock."""
    monkeypatch.setattr(summarize_guard, "COMPACT_INACTIVITY_TIMEOUT_S", 0.03)


async def _compact() -> _Answer:
    return await guarded_compact_summarize(_MESSAGES, cast("BaseChatModel", MagicMock()), "chat-1", None, "")


def _nothing_else_is_running() -> bool:
    return asyncio.all_tasks() == {asyncio.current_task()}


class TestAnAnsweringModel:
    async def test_the_summary_comes_back_and_the_model_call_gets_what_the_compaction_gave_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answer = AsyncMock(return_value=(_KEPT, _SUMMARY))
        _model_answers(monkeypatch, answer)
        earlier = StructuredSummary(user_goal="earlier goal")
        llm = cast("BaseChatModel", MagicMock())

        result = await guarded_compact_summarize(_MESSAGES, llm, "chat-1", earlier, "auth", max_context_tokens=64_000)

        assert result == (_KEPT, _SUMMARY)
        assert answer.await_args is not None
        call = answer.await_args.kwargs
        assert call["messages"] is _MESSAGES
        assert call["llm"] is llm
        assert call["chat_id"] == "chat-1"
        assert call["existing_summary"] is earlier
        assert call["focus_topic"] == "auth"
        assert call["config"].max_context_tokens == 64_000
        assert isinstance(call["progress_tracker"], ProgressClock)
        assert _nothing_else_is_running()

    async def test_a_failure_of_the_model_call_reaches_the_caller_as_it_is(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _model_answers(monkeypatch, AsyncMock(side_effect=RuntimeError("upstream unavailable")))

        with caplog.at_level(logging.WARNING, logger=_GUARD_LOGGER), pytest.raises(RuntimeError, match="upstream unavailable"):
            await _compact()

        assert caplog.records == []
        assert _nothing_else_is_running()

    async def test_a_model_that_keeps_producing_output_is_not_cut_off(
        self, monkeypatch: pytest.MonkeyPatch, fast_watchdog: None
    ) -> None:
        """The run outlasts the inactivity window, yet the model was never silent for it: the guard waits for the answer."""
        clock = _silent_for(monkeypatch, 0.0)

        async def answers_after_three_looks(**_kwargs: object) -> _Answer:
            await clock.looked_three_times.wait()
            return _KEPT, _SUMMARY

        _model_answers(monkeypatch, answers_after_three_looks)

        assert await _compact() == (_KEPT, _SUMMARY)
        assert clock.looks >= 3


class TestAStalledModel:
    async def test_a_model_that_goes_silent_is_cut_off_and_stopped(
        self, monkeypatch: pytest.MonkeyPatch, fast_watchdog: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        _silent_for(monkeypatch, 1.0)
        model = _stuck_model(monkeypatch)

        with caplog.at_level(logging.WARNING, logger=_GUARD_LOGGER), pytest.raises(InactivityTimeoutError):
            await _compact()

        assert model.stopped
        assert "Progress-aware timeout" in caplog.text
        assert _nothing_else_is_running()

    async def test_a_run_past_the_total_ceiling_is_cut_off_even_while_the_model_streams(
        self, monkeypatch: pytest.MonkeyPatch, fast_watchdog: None
    ) -> None:
        monkeypatch.setattr(summarize_guard, "COMPACT_TOTAL_CEILING_S", 0.0)
        _silent_for(monkeypatch, 0.0)
        model = _stuck_model(monkeypatch)

        with pytest.raises(TotalCeilingTimeoutError):
            await _compact()

        assert model.stopped
        assert _nothing_else_is_running()

    async def test_stopping_the_compaction_stops_the_model_call_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        model = _stuck_model(monkeypatch)
        compaction = asyncio.create_task(_compact())
        await model.started.wait()

        compaction.cancel()
        with pytest.raises(asyncio.CancelledError):
            await compaction

        assert model.stopped
        assert _nothing_else_is_running()
