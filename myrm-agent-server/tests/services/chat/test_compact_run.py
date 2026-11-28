"""A manual compaction runs in the background: a request starts it, the page asks for it by its id until it ends.

A compaction has a model summarise a whole history and takes longer than a proxy lets one request wait, so the request
only starts it. Each test here drives a stand-in for the service's ``compact_chat`` that finishes when the test says so.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import patch

import httpx
import pytest
from myrm_agent_harness.toolkits.llms.transport.errors import LLMResponseError

from app.schemas.responses import BusinessCode
from app.services.chat.compact import run as compact_run
from app.services.chat.compact._types import CompactReason, CompactResult
from app.services.chat.compact.run import (
    CompactRun,
    CompactRunState,
    drain_compact_runs,
    get_compact_run,
    start_compact_run,
)
from tests.support.provider_failures import provider_failure

_SECRET = "sk-live-0123456789abcdef"
_COMPACTED = CompactResult(compacted=True, original_tokens=9000, summary_tokens=900, tokens_saved=8100, message_count=24)


class _Session:
    closed = False


@dataclass
class _Compaction:
    """What the service's ``compact_chat`` would do, ended by the test: the answer it was told to give, once released."""

    outcome: CompactResult | Exception = _COMPACTED
    release: asyncio.Event = field(default_factory=asyncio.Event)
    calls: list[tuple[_Session, str, str, bool]] = field(default_factory=list)
    sessions: list[_Session] = field(default_factory=list)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[_Session]:
        opened = _Session()
        self.sessions.append(opened)
        try:
            yield opened
        finally:
            opened.closed = True

    async def __call__(
        self, db: _Session, chat_id: str, *, focus_topic: str = "", explicit_request: bool = False
    ) -> CompactResult:
        self.calls.append((db, chat_id, focus_topic, explicit_request))
        await self.release.wait()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def finish(self, outcome: CompactResult | Exception | None = None) -> None:
        if outcome is not None:
            self.outcome = outcome
        self.release.set()


@pytest.fixture
def compaction() -> Iterator[_Compaction]:
    stand_in = _Compaction()
    with (
        patch("app.services.chat.compact_service.compact_chat", new=stand_in),
        patch.object(compact_run, "get_session", new=stand_in.session),
    ):
        yield stand_in


async def _ended(run: CompactRun) -> CompactRun:
    """Wait for the run to leave ``running``, which a run does within a few loop turns once its compaction answered."""
    deadline = time.monotonic() + 5.0
    while run.state is CompactRunState.RUNNING:
        assert time.monotonic() < deadline, "the run is still running"
        await asyncio.sleep(0.005)
    return run


async def _begun(compaction: _Compaction) -> None:
    """Wait until the run's task has called the service."""
    deadline = time.monotonic() + 5.0
    while not compaction.calls:
        assert time.monotonic() < deadline, "the compaction was never started"
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_a_run_starts_at_once_however_long_the_compaction_takes(compaction: _Compaction) -> None:
    started = time.monotonic()
    run = start_compact_run("chat-1", "")

    assert time.monotonic() - started < 0.5
    assert run.state is CompactRunState.RUNNING
    assert get_compact_run("chat-1", run.run_id) is run
    compaction.finish()
    await _ended(run)


@pytest.mark.asyncio
async def test_a_finished_run_carries_what_the_service_answered(compaction: _Compaction) -> None:
    run = start_compact_run("chat-1", "billing")
    await _begun(compaction)

    assert run.state is CompactRunState.RUNNING
    compaction.finish()
    ended = await _ended(run)

    assert ended.state is CompactRunState.SUCCEEDED
    assert ended.result == _COMPACTED
    assert ended.failure is None
    assert ended.focus_topic == "billing"


@pytest.mark.asyncio
async def test_a_declined_compaction_is_a_finished_run_with_the_reason(compaction: _Compaction) -> None:
    declined = CompactResult(compacted=False, message_count=3, reason=CompactReason.TOO_FEW_MESSAGES)
    compaction.finish(declined)

    run = await _ended(start_compact_run("chat-1", ""))

    assert (run.state, run.result) == (CompactRunState.SUCCEEDED, declined)


@pytest.mark.asyncio
async def test_the_compaction_is_a_persons_request_on_a_session_of_the_runs_own(compaction: _Compaction) -> None:
    run = start_compact_run("chat-1", "billing")
    await _begun(compaction)

    [(db, chat_id, focus_topic, explicit_request)] = compaction.calls
    assert (chat_id, focus_topic, explicit_request) == ("chat-1", "billing", True)
    assert not db.closed
    compaction.finish()
    await _ended(run)

    assert db is compaction.sessions[0]
    assert db.closed


@pytest.mark.asyncio
async def test_a_run_that_ended_holds_no_task(compaction: _Compaction) -> None:
    run = start_compact_run("chat-1", "")
    assert run.task is not None
    compaction.finish()
    await _ended(run)
    await asyncio.sleep(0)

    assert run.task is None


@pytest.mark.asyncio
async def test_a_second_request_for_the_same_chat_joins_the_run_in_flight(compaction: _Compaction) -> None:
    first = start_compact_run("chat-1", "billing")
    second = start_compact_run("chat-1", "something else")
    await _begun(compaction)
    compaction.finish()
    await _ended(first)

    assert second is first
    assert len(compaction.calls) == 1
    assert first.focus_topic == "billing"


@pytest.mark.asyncio
async def test_chats_compact_side_by_side(compaction: _Compaction) -> None:
    one = start_compact_run("chat-1", "")
    other = start_compact_run("chat-2", "")
    compaction.finish()
    await _ended(one)
    await _ended(other)

    assert one is not other
    assert sorted(call[1] for call in compaction.calls) == ["chat-1", "chat-2"]
    assert (get_compact_run("chat-1", one.run_id), get_compact_run("chat-2", other.run_id)) == (one, other)


@pytest.mark.asyncio
async def test_the_next_run_of_a_chat_is_a_run_of_its_own_and_the_one_that_ended_stays_readable(
    compaction: _Compaction,
) -> None:
    """The page that started the first run asks for it after a second page's run of the same chat has ended."""
    declined = CompactResult(compacted=False, message_count=3, reason=CompactReason.TOO_FEW_MESSAGES)
    compaction.finish()
    first = await _ended(start_compact_run("chat-1", "first"))
    compaction.outcome = declined
    second = await _ended(start_compact_run("chat-1", "second"))

    assert second.run_id != first.run_id
    assert get_compact_run("chat-1", first.run_id) is first
    assert get_compact_run("chat-1", second.run_id) is second
    assert (first.result, second.result) == (_COMPACTED, declined)


@pytest.mark.asyncio
async def test_a_failed_run_is_not_joined_the_next_request_starts_afresh(compaction: _Compaction) -> None:
    compaction.finish(RuntimeError("boom"))
    failed = await _ended(start_compact_run("chat-1", ""))

    compaction.outcome = _COMPACTED
    retry = start_compact_run("chat-1", "")

    assert failed.state is CompactRunState.FAILED
    assert retry is not failed
    assert (await _ended(retry)).state is CompactRunState.SUCCEEDED


def test_a_run_the_registry_does_not_hold_is_not_found() -> None:
    assert get_compact_run("chat-1", "never-started") is None


@pytest.mark.asyncio
async def test_a_run_is_found_only_under_the_chat_it_belongs_to(compaction: _Compaction) -> None:
    compaction.finish()
    run = await _ended(start_compact_run("chat-1", ""))

    assert get_compact_run("chat-2", run.run_id) is None
    assert get_compact_run("chat-1", run.run_id) is run


@pytest.fixture
def history_of_three() -> Iterator[None]:
    with patch.object(compact_run, "COMPACT_RUN_HISTORY", 3):
        yield


@pytest.mark.asyncio
async def test_the_registry_keeps_the_newest_runs_and_drops_the_oldest_ended_ones(
    compaction: _Compaction, history_of_three: None
) -> None:
    compaction.finish()
    runs = [await _ended(start_compact_run(f"chat-{n}", "")) for n in range(5)]

    assert [get_compact_run(run.chat_id, run.run_id) is run for run in runs] == [False, False, True, True, True]


@pytest.mark.asyncio
async def test_a_run_still_going_is_never_dropped_whatever_the_history_holds(
    compaction: _Compaction, history_of_three: None
) -> None:
    going = [start_compact_run(f"chat-{n}", "") for n in range(5)]

    assert [get_compact_run(run.chat_id, run.run_id) is run for run in going] == [True] * 5

    compaction.finish()
    for run in going:
        await _ended(run)
    newest = start_compact_run("chat-5", "")

    assert [get_compact_run(run.chat_id, run.run_id) is run for run in [*going, newest]] == [False] * 3 + [True] * 3
    await _ended(newest)


@pytest.mark.asyncio
async def test_an_invalid_request_fails_the_run_as_a_400(compaction: _Compaction) -> None:
    compaction.finish(ValueError("Cannot resolve before_message_id for chat_id=chat-1"))

    run = await _ended(start_compact_run("chat-1", ""))

    assert run.state is CompactRunState.FAILED
    assert run.result is None
    assert run.failure is not None
    assert (run.failure.status_code, run.failure.code) == (400, BusinessCode.VALIDATION_ERROR.value)
    assert "before_message_id" in run.failure.message


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (provider_failure(401, f"{_SECRET} is not valid for org-acme"), BusinessCode.AI_AUTH_ERROR),
        (provider_failure(429, f"{_SECRET} is not valid for org-acme"), BusinessCode.AI_RATE_LIMIT_ERROR),
        (provider_failure(503, f"{_SECRET} is not valid for org-acme"), BusinessCode.AI_MODEL_ERROR),
        (LLMResponseError("openai", "openai answered with a non-JSON body: '<html>'"), BusinessCode.AI_MODEL_ERROR),
        (httpx.ConnectTimeout("provider unreachable"), BusinessCode.AI_TIMEOUT_ERROR),
    ],
)
@pytest.mark.asyncio
async def test_a_failed_summary_model_fails_the_run_as_a_500_named_by_what_the_provider_answered(
    compaction: _Compaction, failure: Exception, code: BusinessCode
) -> None:
    compaction.finish(failure)

    run = await _ended(start_compact_run("chat-1", ""))

    assert run.state is CompactRunState.FAILED
    assert run.failure is not None
    assert (run.failure.status_code, run.failure.code) == (500, code.value)
    assert run.failure.message.startswith("Compact chat context failed: AI ")
    assert _SECRET not in run.failure.message


@pytest.mark.asyncio
async def test_a_run_cut_off_midway_is_a_failed_run_not_one_that_runs_for_ever(compaction: _Compaction) -> None:
    run = start_compact_run("chat-1", "")
    await _begun(compaction)
    task = run.task
    assert task is not None

    task.cancel()
    await asyncio.wait([task])
    await asyncio.sleep(0)

    assert run.state is CompactRunState.FAILED
    assert run.failure is not None
    assert run.failure.code == BusinessCode.INTERNAL_ERROR.value
    assert run.task is None
    assert compaction.sessions[0].closed


@pytest.mark.asyncio
async def test_a_run_cancelled_before_it_began_is_failed_too(compaction: _Compaction) -> None:
    run = start_compact_run("chat-1", "")
    task = run.task
    assert task is not None

    task.cancel()
    await asyncio.wait([task])
    await asyncio.sleep(0)

    assert run.state is CompactRunState.FAILED
    assert compaction.calls == []


@pytest.mark.asyncio
async def test_a_drain_waits_for_the_run_in_flight(compaction: _Compaction) -> None:
    run = start_compact_run("chat-1", "")
    await _begun(compaction)
    asyncio.get_running_loop().call_later(0.05, compaction.finish)

    started = time.monotonic()
    await drain_compact_runs(timeout_s=5.0)

    assert 0.04 < time.monotonic() - started < 2.0
    assert run.state is CompactRunState.SUCCEEDED


@pytest.mark.asyncio
async def test_a_drain_gives_up_at_the_bound_and_says_so(compaction: _Compaction, caplog: pytest.LogCaptureFixture) -> None:
    run = start_compact_run("chat-1", "")
    await _begun(compaction)

    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger=compact_run.logger.name):
        await drain_compact_runs(timeout_s=0.05)

    assert time.monotonic() - started < 2.0
    assert run.state is CompactRunState.RUNNING
    assert "still running" in caplog.text
    compaction.finish()
    await _ended(run)


@pytest.mark.asyncio
async def test_a_drain_with_nothing_running_returns_at_once(compaction: _Compaction) -> None:
    compaction.finish()
    await _ended(start_compact_run("chat-1", ""))

    started = time.monotonic()
    await drain_compact_runs(timeout_s=5.0)

    assert time.monotonic() - started < 0.5
