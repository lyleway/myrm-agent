"""A model call that read the provider's prompt cache records what the cache saved, on every call path.

The model is the real ``TransportChatModel`` over a scripted client; prices come from the catalog, and the tracker and
the ledger are the request-scoped ones the adapter writes to.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import BaseMessage, HumanMessage

from myrm_agent_harness.toolkits.llms.adapters.chat_model import TransportChatModel
from myrm_agent_harness.toolkits.llms.adapters.chat_model.usage_recording import record_usage
from myrm_agent_harness.toolkits.llms.catalog import register_model, store
from myrm_agent_harness.utils.token_economics.tracker import (
    get_pending_token_events,
    get_token_tracker,
    init_token_tracker,
    reset_token_tracker,
    set_usage_ledger,
)
from myrm_agent_harness.utils.token_economics.usage_ledger import UsageRecord

_PRICED = "openai/cache-priced-model"
_UNPRICED = "openai/cache-unpriced-model"
_CALL_PATHS = ["generate", "agenerate", "stream", "astream"]

# Prompt 20000 with 15000 read from the cache and 50 generated, at 3/M input, 0.3/M cache read and 15/M output:
# without the cache 20000 * 3e-6 = 0.06 of prompt; with it 5000 * 3e-6 + 15000 * 3e-7 = 0.0195, so 0.0405 is saved.
_SAVED = 0.0405
_BILL = 0.02025


class _Ledger:
    """Stands in for the request-scoped audit ledger and keeps what was appended to it."""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def append(self, record: object) -> None:
        assert isinstance(record, UsageRecord)
        self.records.append(record)


@pytest.fixture(autouse=True)
def _request_scope(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Ledger]:
    monkeypatch.setattr(store, "_REGISTERED", {})
    register_model(
        _PRICED,
        {
            "input_cost_per_token": 3e-06,
            "output_cost_per_token": 1.5e-05,
            "cache_read_input_token_cost": 3e-07,
            "cache_creation_input_token_cost": 3.75e-06,
        },
    )
    init_token_tracker()
    ledger = _Ledger()
    set_usage_ledger(ledger)
    yield ledger
    reset_token_tracker()


def _usage(prompt: int, *, cached: object = 0, completion: int = 50) -> dict[str, object]:
    usage: dict[str, object] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if cached:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return usage


def _response(usage: dict[str, object]) -> dict[str, object]:
    message = {"role": "assistant", "content": "Hello"}
    return {
        "id": "chatcmpl-1",
        "model": "cache-test-model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        "usage": usage,
    }


def _chunks(usage: dict[str, object]) -> list[dict[str, object]]:
    def chunk(choices: list[dict[str, object]], **extra: object) -> dict[str, object]:
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "model": "cache-test-model",
            "choices": choices,
            **extra,
        }

    return [
        chunk([{"index": 0, "delta": {"role": "assistant", "content": "Hello"}, "finish_reason": None}]),
        chunk([{"index": 0, "delta": {"content": " world"}, "finish_reason": "stop"}]),
        chunk([], usage=usage),
    ]


async def _async_chunks(usage: dict[str, object]) -> AsyncIterator[dict[str, object]]:
    for chunk in _chunks(usage):
        yield chunk


async def _call(path: str, usage: dict[str, object], *, model_name: str = _PRICED) -> None:
    """Make one model call down the named path; every path ends in the same usage recording."""
    model = TransportChatModel(model=model_name)
    model.client = MagicMock()
    messages: list[BaseMessage] = [HumanMessage(content="hi")]
    if path == "generate":
        model.client.completion.return_value = _response(usage)
        model._generate(messages)
    elif path == "agenerate":
        model.client.acompletion = AsyncMock(return_value=_response(usage))
        await model._agenerate(messages)
    elif path == "stream":
        model.client.completion.return_value = iter(_chunks(usage))
        list(model._stream(messages))
    else:
        model.client.acompletion = AsyncMock(return_value=_async_chunks(usage))
        async for _ in model._astream(messages):
            pass


def _economics() -> dict[str, object]:
    tracker = get_token_tracker()
    assert tracker is not None
    return tracker.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _CALL_PATHS)
async def test_a_call_that_read_the_cache_reports_what_it_saved(path: str, _request_scope: _Ledger) -> None:
    await _call(path, _usage(20_000, cached=15_000))

    (event,) = get_pending_token_events()
    usage = cast(dict[str, object], event["usage"])
    assert event["cache_savings_usd"] == pytest.approx(_SAVED)
    assert usage["net_savings_usd"] == pytest.approx(_SAVED)
    assert usage["has_effective_cache"] is True
    economics = _economics()
    assert economics["total_cache_savings_usd"] == pytest.approx(_SAVED)
    assert economics["has_effective_cache"] is True
    (record,) = _request_scope.records
    assert record.cache_savings_usd == pytest.approx(_SAVED)
    assert record.cost_usd == pytest.approx(_BILL)


@pytest.mark.asyncio
async def test_the_savings_follow_the_model_that_earned_them() -> None:
    await _call("stream", _usage(20_000, cached=15_000))

    breakdown = cast(dict[str, dict[str, object]], _economics()["model_breakdown"])
    assert breakdown["cache-test-model"]["cache_savings_usd"] == pytest.approx(_SAVED)


@pytest.mark.asyncio
async def test_a_call_that_read_no_cache_reports_no_savings(_request_scope: _Ledger) -> None:
    await _call("stream", _usage(20_000))

    (event,) = get_pending_token_events()
    assert "cache_savings_usd" not in event
    assert cast(dict[str, object], event["usage"])["has_effective_cache"] is False
    assert _economics()["total_cache_savings_usd"] == 0.0
    assert [record.cache_savings_usd for record in _request_scope.records] == [0.0]


@pytest.mark.parametrize("count", [float("nan"), float("inf"), "many", True], ids=["nan", "inf", "text", "bool"])
def test_a_cache_count_that_is_not_a_number_is_no_cache_and_the_call_is_still_recorded(count: object) -> None:
    record_usage(TransportChatModel(model=_PRICED), _usage(20_000, cached=count), model_name="cache-test-model")

    tracker = get_token_tracker()
    assert tracker is not None
    assert tracker.call_count == 1
    assert _economics()["total_cache_savings_usd"] == 0.0


@pytest.mark.asyncio
async def test_an_unpriced_model_reports_no_savings_and_an_unknown_cost(_request_scope: _Ledger) -> None:
    await _call("stream", _usage(20_000, cached=15_000), model_name=_UNPRICED)

    economics = _economics()
    assert (economics["total_cache_savings_usd"], economics["cost_status"]) == (0.0, "unknown")
    assert [record.cache_savings_usd for record in _request_scope.records] == [0.0]
