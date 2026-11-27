"""The proxy on the production chat model: the output cap reaches the provider call and the cost is what the call used.

The unit tests of the proxy stand in for the model; these run the real ``TransportChatModel`` with only the transport
faked, because the proxy relies on two behaviours of it: a per-call ``max_tokens`` overrides the model's own, and every
attempt reports its usage and price to the request's token tracker.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from myrm_agent_harness.toolkits.llms.adapters.chat_model import TransportChatModel
from myrm_agent_harness.utils.token_economics.cost_engine import compute_cost_by_tokens
from myrm_agent_harness.utils.token_economics.multidim_budget import BudgetDimension, MultidimensionalBudgetGuard
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models import Base
from app.services.artifact_apps import ai_proxy, ai_usage
from app.services.artifact_apps.ai_proxy import AiRequest, run_ai
from app.services.budget.budget_day import budget_day_start_utc

_MODEL = "gpt-4o-mini"
_USAGE = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}


def _completion(content: str, usage: dict[str, int]) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": usage,
    }


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[MultidimensionalBudgetGuard]:
    daily = MultidimensionalBudgetGuard(daily=BudgetDimension(limit_usd=10.0))
    monkeypatch.setattr(ai_proxy, "should_block_execution", AsyncMock(return_value=False))
    monkeypatch.setattr(ai_proxy, "get_budget_guard", AsyncMock(return_value=daily))
    monkeypatch.setattr(ai_proxy, "get_budget_guard_sync", lambda: daily)
    yield daily


def _model(*responses: dict[str, object]) -> tuple[TransportChatModel, AsyncMock]:
    model = TransportChatModel(model=_MODEL, max_tokens=8000)
    acompletion = AsyncMock(side_effect=list(responses))
    model.client = MagicMock(acompletion=acompletion)
    return model, acompletion


async def test_the_pages_output_cap_overrides_the_models_own_on_the_provider_call(
    db: AsyncSession, guard: MultidimensionalBudgetGuard
) -> None:
    model, acompletion = _model(_completion("Sunny", _USAGE))

    await run_ai(db, "a1", AiRequest(prompt="Weather?", system=None, max_tokens=77), model)

    assert acompletion.await_args is not None
    assert acompletion.await_args.kwargs["max_tokens"] == 77


async def test_the_charge_is_the_price_of_the_tokens_the_call_used(db: AsyncSession, guard: MultidimensionalBudgetGuard) -> None:
    model, _ = _model(_completion("Sunny", _USAGE))
    expected = compute_cost_by_tokens(_MODEL, 120, 30).usd

    result = await run_ai(db, "a1", AiRequest(prompt="Weather?", system=None, max_tokens=256), model)

    assert result.text == "Sunny"
    assert result.cost_usd == pytest.approx(expected)
    assert await ai_usage.spent_since(db, budget_day_start_utc(), "a1") == pytest.approx(expected)
    assert guard.daily_cost == pytest.approx(expected)


async def test_an_attempt_the_adapter_repeats_after_an_empty_reply_is_not_lost_from_the_charge(
    db: AsyncSession, guard: MultidimensionalBudgetGuard
) -> None:
    """The adapter asks again when the provider answers without a choice; the call is charged for the tokens of the answer it returns,
    and never for less than that."""
    model, acompletion = _model({"choices": [], "usage": {}}, _completion("Sunny", _USAGE))
    expected = compute_cost_by_tokens(_MODEL, 120, 30).usd

    result = await run_ai(db, "a1", AiRequest(prompt="Weather?", system=None, max_tokens=256), model)

    assert acompletion.await_count == 2
    assert result.text == "Sunny"
    assert result.cost_usd >= expected
