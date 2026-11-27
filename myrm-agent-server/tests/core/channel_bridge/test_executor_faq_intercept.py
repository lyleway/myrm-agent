"""The FAQ lookup of the channel executor: a hit answers and is counted, a miss is counted, a failure never stops the turn.

The interceptor and the hit tracker are optional services that sit between an inbound message and the agent run, so a
lookup that cannot run must end in "no answer" (the agent takes the message), not in an error reply to the member.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.channel_bridge.agent_executor.executor import ChannelAgentExecutor

_FACTORY = "app.services.faq.factory.get_faq_interceptor"
_TRACKER = "app.services.faq.tracker.FaqHitTracker"


async def _lookup() -> str | None:
    return await ChannelAgentExecutor._try_faq_intercept(agent_id="agent-1", user_query="opening hours?", channel="telegram")


def _interceptor(match: tuple[SimpleNamespace | None, str | None, float]) -> SimpleNamespace:
    """An interceptor whose semantic match is ``match``: the hit (or none), the corpus searched and the best score."""
    return SimpleNamespace(try_match=AsyncMock(return_value=match))


@pytest.fixture
def tracker() -> Iterator[SimpleNamespace]:
    """The hit tracker the lookup records into; ``record`` is the awaited call to inspect."""
    record = AsyncMock()
    with patch(_TRACKER, new=MagicMock(return_value=SimpleNamespace(record=record))):
        yield SimpleNamespace(record=record)


@pytest.mark.asyncio
async def test_a_hit_answers_and_is_counted_against_its_entry(tracker: SimpleNamespace) -> None:
    hit = SimpleNamespace(answer="We open at 9.", corpus_id="corpus-1", entry_id="entry-4", score=0.93)
    interceptor = _interceptor((hit, "corpus-1", 0.93))

    with patch(_FACTORY, new=AsyncMock(return_value=interceptor)):
        assert await _lookup() == "We open at 9."

    interceptor.try_match.assert_awaited_once_with("agent-1", "opening hours?", channel="telegram")
    tracker.record.assert_awaited_once_with(
        corpus_id="corpus-1", channel="telegram", user_query="opening hours?", top_score=0.93, entry_id="entry-4", hit=True
    )


@pytest.mark.asyncio
async def test_a_miss_in_a_searched_corpus_is_counted_and_gives_no_answer(tracker: SimpleNamespace) -> None:
    with patch(_FACTORY, new=AsyncMock(return_value=_interceptor((None, "corpus-1", 0.41)))):
        assert await _lookup() is None

    tracker.record.assert_awaited_once_with(
        corpus_id="corpus-1", channel="telegram", user_query="opening hours?", top_score=0.41, hit=False
    )


@pytest.mark.asyncio
async def test_a_miss_in_no_corpus_counts_nothing(tracker: SimpleNamespace) -> None:
    """An agent without a FAQ corpus has nothing to count a miss against."""
    with patch(_FACTORY, new=AsyncMock(return_value=_interceptor((None, None, 0.0)))):
        assert await _lookup() is None

    tracker.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_an_interceptor_the_message_goes_to_the_agent(tracker: SimpleNamespace) -> None:
    with patch(_FACTORY, new=AsyncMock(return_value=None)):
        assert await _lookup() is None

    tracker.record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["interceptor-cannot-be-built", "lookup-raises"])
async def test_a_lookup_that_cannot_run_gives_no_answer_instead_of_an_error(tracker: SimpleNamespace, failure: str) -> None:
    if failure == "interceptor-cannot-be-built":
        factory = AsyncMock(side_effect=RuntimeError("no embedding model"))
    else:
        factory = AsyncMock(return_value=SimpleNamespace(try_match=AsyncMock(side_effect=RuntimeError("index offline"))))

    with patch(_FACTORY, new=factory):
        assert await _lookup() is None

    tracker.record.assert_not_awaited()
