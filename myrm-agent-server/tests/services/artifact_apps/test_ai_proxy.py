"""The host model-call proxy: what a call may cost, how it is charged, and that a refused call never reaches the model."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from myrm_agent_harness.utils.token_economics.multidim_budget import BudgetDimension, MultidimensionalBudgetGuard
from myrm_agent_harness.utils.token_economics.tracker import record_token_usage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models import Base
from app.services.artifact_apps import ai_proxy, ai_usage
from app.services.artifact_apps.ai_proxy import AiRejected, AiRequest, run_ai
from app.services.budget.budget_day import budget_day_start_utc

_PRICED_MODEL = "gpt-4o-mini"
_REQUEST = AiRequest(prompt="What is the weather?", system=None, max_tokens=256)


class _Model:
    """Reports what a real call reports to the request's token tracker."""

    def __init__(self, *, text: str = "Sunny", cost_usd: float = 0.0004, model_name: str = _PRICED_MODEL) -> None:
        self.model_name = model_name
        self.custom_llm_provider = None
        self._text = text
        self._cost_usd = cost_usd
        self.seen: list[tuple[list[BaseMessage], int]] = []

    async def ainvoke(self, input: list[BaseMessage], *, max_tokens: int) -> AIMessage:
        self.seen.append((input, max_tokens))
        record_token_usage({"prompt_tokens": 20, "completion_tokens": 5}, cost_usd=self._cost_usd, cost_status="actual")
        return AIMessage(content=self._text)


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
    """The user's overall budget: an enabled process guard with $10 a day, and no block policy."""
    daily = MultidimensionalBudgetGuard(daily=BudgetDimension(limit_usd=10.0))
    monkeypatch.setattr(ai_proxy, "should_block_execution", AsyncMock(return_value=False))
    monkeypatch.setattr(ai_proxy, "get_budget_guard", AsyncMock(return_value=daily))
    monkeypatch.setattr(ai_proxy, "get_budget_guard_sync", lambda: daily)
    yield daily


async def _spent(db: AsyncSession) -> float:
    return await ai_usage.spent_since(db, budget_day_start_utc(), "a1")


async def test_a_completed_call_returns_the_text_and_is_charged_what_it_cost(
    db: AsyncSession, guard: MultidimensionalBudgetGuard
) -> None:
    model = _Model(text="  Sunny \n")

    result = await run_ai(db, "a1", _REQUEST, model)

    assert result.text == "Sunny"
    assert result.cost_usd == pytest.approx(0.0004)
    assert await _spent(db) == pytest.approx(0.0004)
    assert guard.daily_cost == pytest.approx(0.0004)


async def test_the_page_gets_a_plain_completion_with_its_own_output_cap(
    db: AsyncSession, guard: MultidimensionalBudgetGuard
) -> None:
    model = _Model()

    await run_ai(db, "a1", AiRequest(prompt="Hi", system="Be brief", max_tokens=77), model)

    ((messages, max_tokens),) = model.seen
    assert max_tokens == 77
    assert [type(message) for message in messages] == [SystemMessage, HumanMessage]
    assert [message.content for message in messages] == ["Be brief", "Hi"]


async def test_a_model_without_a_known_price_is_refused_before_anything_is_charged(
    db: AsyncSession, guard: MultidimensionalBudgetGuard
) -> None:
    model = _Model(model_name="llama-local")

    with pytest.raises(AiRejected) as refused:
        await run_ai(db, "a1", _REQUEST, model)

    assert refused.value.code == "unavailable"
    assert model.seen == []
    assert await _spent(db) == 0.0


async def test_a_call_over_the_apps_daily_cap_never_reaches_the_model(
    db: AsyncSession, guard: MultidimensionalBudgetGuard
) -> None:
    await ai_usage.reserve(db, "a1", ai_proxy.APP_DAILY_BUDGET_USD, ai_proxy.APP_DAILY_BUDGET_USD)
    model = _Model()

    with pytest.raises(AiRejected) as refused:
        await run_ai(db, "a1", _REQUEST, model)

    assert refused.value.code == "budget_exceeded"
    assert model.seen == []


async def test_the_users_block_policy_stops_the_call(
    db: AsyncSession, guard: MultidimensionalBudgetGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_proxy, "should_block_execution", AsyncMock(return_value=True))
    model = _Model()

    with pytest.raises(AiRejected) as refused:
        await run_ai(db, "a1", _REQUEST, model)

    assert (refused.value.code, model.seen, await _spent(db)) == ("budget_exceeded", [], 0.0)


@pytest.mark.parametrize(("spent", "allowed"), [(8.0, True), (8.6, False), (9.9, False), (10.0, False)])
async def test_an_app_call_does_not_use_the_reserve_an_agent_run_wraps_up_with(
    db: AsyncSession, guard: MultidimensionalBudgetGuard, spent: float, allowed: bool
) -> None:
    guard.record_cost(spent)
    model = _Model()

    if allowed:
        await run_ai(db, "a1", _REQUEST, model)
        assert len(model.seen) == 1
    else:
        with pytest.raises(AiRejected) as refused:
            await run_ai(db, "a1", _REQUEST, model)
        assert (refused.value.code, model.seen) == ("budget_exceeded", [])


async def test_a_failed_call_keeps_the_charge_it_was_reserved_with(db: AsyncSession, guard: MultidimensionalBudgetGuard) -> None:
    model = _Model()
    model.ainvoke = AsyncMock(side_effect=RuntimeError("provider down"))  # type: ignore[method-assign]

    with pytest.raises(AiRejected) as refused:
        await run_ai(db, "a1", _REQUEST, model)

    assert refused.value.code == "upstream_error"
    assert "provider down" not in str(refused.value)
    assert await _spent(db) > 0.0
    assert guard.daily_cost == pytest.approx(await _spent(db))


async def test_a_call_that_takes_too_long_is_stopped_and_stays_charged(
    db: AsyncSession, guard: MultidimensionalBudgetGuard, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_proxy, "CALL_TIMEOUT_S", 0.05)
    model = _Model()

    async def hang(input: list[BaseMessage], *, max_tokens: int) -> AIMessage:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    model.ainvoke = hang  # type: ignore[method-assign]

    with pytest.raises(AiRejected) as refused:
        await run_ai(db, "a1", _REQUEST, model)

    assert refused.value.code == "timeout"
    assert await _spent(db) > 0.0


async def test_cancelling_a_call_does_not_give_the_budget_back(db: AsyncSession, guard: MultidimensionalBudgetGuard) -> None:
    started = asyncio.Event()
    model = _Model()

    async def hang(input: list[BaseMessage], *, max_tokens: int) -> AIMessage:
        started.set()
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    model.ainvoke = hang  # type: ignore[method-assign]
    task = asyncio.create_task(run_ai(db, "a1", _REQUEST, model))
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _spent(db) > 0.0
    assert guard.daily_cost > 0.0


async def test_a_reply_without_usage_is_charged_what_it_was_reserved_with(
    db: AsyncSession, guard: MultidimensionalBudgetGuard
) -> None:
    model = _Model(cost_usd=0.0)

    result = await run_ai(db, "a1", _REQUEST, model)

    assert result.cost_usd > 0.0
    assert await _spent(db) == pytest.approx(result.cost_usd)


async def test_the_budget_works_without_a_process_guard(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_proxy, "should_block_execution", AsyncMock(return_value=False))
    monkeypatch.setattr(ai_proxy, "get_budget_guard", AsyncMock(return_value=None))
    monkeypatch.setattr(ai_proxy, "get_budget_guard_sync", lambda: None)

    result = await run_ai(db, "a1", _REQUEST, _Model())

    assert result.text == "Sunny"
