"""HTTP surface of the host model-call proxy: grant gate, limits, logging and that a page's prompt is never stored."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from myrm_agent_harness.utils.token_economics.tracker import record_token_usage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.artifact_apps import ai_proxy, ai_usage
from app.services.artifact_apps.rate_limit import AI_LIMITER
from app.services.budget.budget_day import budget_day_start_utc
from app.services.risk.refusal import RISK_HELD_CODE
from tests.support.risk_gate import API_KEY_RULE, SECRET, build_service_with_rules

# The package re-exports the APIRouter under the name of its module, so the module itself is imported by path.
router_module = importlib.import_module("app.api.artifact_apps.router")

_SHA = "a" * 64
_PROMPT = "my secret diary entry"
_PAGE_REQUEST = {"version_sha256": _SHA, "prompt": _PROMPT, "max_tokens": 128}
_AI = "/api/v1/artifact-apps/clock/ai"


class _Model:
    model_name = "gpt-4o-mini"
    custom_llm_provider = None

    def __init__(self, model_name: str = "gpt-4o-mini") -> None:
        self.model_name = model_name
        self.calls = 0

    async def ainvoke(self, input: list[BaseMessage], *, max_tokens: int) -> AIMessage:
        self.calls += 1
        record_token_usage({"prompt_tokens": 20, "completion_tokens": 5}, cost_usd=0.002, cost_status="actual")
        return AIMessage(content="Sunny")


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Model]:
    fake = _Model()
    monkeypatch.setattr(router_module, "get_llm_for_user", AsyncMock(return_value=fake))
    monkeypatch.setattr(ai_proxy, "should_block_execution", AsyncMock(return_value=False))
    monkeypatch.setattr(ai_proxy, "get_budget_guard", AsyncMock(return_value=None))
    monkeypatch.setattr(ai_proxy, "get_budget_guard_sync", lambda: None)
    yield fake


async def _install_and_allow(client: httpx.AsyncClient, decision: str = "allow") -> None:
    await client.put("/api/v1/artifact-apps/clock")
    await client.put("/api/v1/artifact-apps/clock/grants/ai", json={"decision": decision, "scope": [], "version_sha256": _SHA})


async def _ask(client: httpx.AsyncClient, **overrides: object) -> dict[str, object]:
    reply = await client.post(_AI, json={**_PAGE_REQUEST, **overrides})
    assert reply.status_code == 200
    data: dict[str, object] = reply.json()["data"]
    return data


async def _log(client: httpx.AsyncClient) -> list[dict[str, object]]:
    log: list[dict[str, object]] = (await client.get("/api/v1/artifact-apps/clock/activity")).json()["data"]
    return log


async def test_an_allowed_app_gets_the_answer_and_the_call_is_logged_with_its_cost_only(
    client: httpx.AsyncClient, model: _Model
) -> None:
    await _install_and_allow(client)

    reply = await _ask(client)

    assert reply == {"ok": True, "text": "Sunny", "code": None, "message": None}
    (entry,) = await _log(client)
    assert (entry["capability"], entry["outcome"], entry["detail"]) == ("ai", "ok", "$0.0020")
    assert _PROMPT not in str(await _log(client))


async def test_a_page_of_an_uninstalled_app_cannot_reach_the_model(client: httpx.AsyncClient, model: _Model) -> None:
    assert (await client.post(_AI, json=_PAGE_REQUEST)).status_code == 404
    assert model.calls == 0


@pytest.mark.parametrize("decision", ["deny", None])
async def test_without_the_users_allowance_the_model_is_never_called(
    client: httpx.AsyncClient, model: _Model, decision: str | None
) -> None:
    if decision is None:
        await client.put("/api/v1/artifact-apps/clock")
    else:
        await _install_and_allow(client, decision)

    reply = await _ask(client)

    assert (reply["ok"], reply["code"]) == (False, "permission_denied")
    assert model.calls == 0
    assert [(e["capability"], e["outcome"]) for e in await _log(client)] == [("ai", "denied")]


@pytest.mark.parametrize("field", ["prompt", "system"])
async def test_a_text_a_risk_rule_holds_never_reaches_the_model(client: httpx.AsyncClient, model: _Model, field: str) -> None:
    await _install_and_allow(client)

    with patch("app.services.risk.detection.get_detection_service", return_value=build_service_with_rules([API_KEY_RULE])):
        reply = await client.post(_AI, json={**_PAGE_REQUEST, field: f"key {SECRET}"})

    assert reply.status_code == 400
    assert reply.json()["detail"]["code"] == RISK_HELD_CODE
    assert SECRET not in reply.text
    assert model.calls == 0
    assert [(e["capability"], e["outcome"], e["detail"]) for e in await _log(client)] == [("ai", "denied", RISK_HELD_CODE)]
    assert AI_LIMITER._in_flight["clock"] == 0


async def test_a_model_without_a_known_price_is_refused_and_logged_as_an_error(
    client: httpx.AsyncClient, model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _install_and_allow(client)
    monkeypatch.setattr(router_module, "get_llm_for_user", AsyncMock(return_value=_Model("llama-local")))

    reply = await _ask(client)

    assert (reply["ok"], reply["code"]) == (False, "unavailable")
    assert [e["outcome"] for e in await _log(client)] == ["error"]


async def test_without_a_configured_model_the_call_is_unavailable(
    client: httpx.AsyncClient, model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _install_and_allow(client)
    monkeypatch.setattr(router_module, "get_llm_for_user", AsyncMock(side_effect=RuntimeError("no provider")))

    reply = await _ask(client)

    assert (reply["ok"], reply["code"], reply["message"]) == (False, "unavailable", "No model is configured")


async def test_an_app_that_used_its_day_is_refused_as_denied(
    client: httpx.AsyncClient, model: _Model, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _install_and_allow(client)
    async with session_factory() as db:
        await ai_usage.reserve(db, "clock", ai_proxy.APP_DAILY_BUDGET_USD, ai_proxy.APP_DAILY_BUDGET_USD)

    reply = await _ask(client)

    assert (reply["ok"], reply["code"]) == (False, "budget_exceeded")
    assert model.calls == 0
    assert [e["outcome"] for e in await _log(client)] == ["denied"]


async def test_the_spend_of_a_call_counts_toward_the_apps_day(
    client: httpx.AsyncClient, model: _Model, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _install_and_allow(client)

    await _ask(client)

    async with session_factory() as db:
        assert await ai_usage.spent_since(db, budget_day_start_utc(), "clock") == pytest.approx(0.002)


async def test_a_burst_over_the_rate_limit_is_refused_and_logged(client: httpx.AsyncClient, model: _Model) -> None:
    await _install_and_allow(client)
    for _ in range(20):
        assert (await _ask(client))["ok"] is True

    reply = await _ask(client)

    assert (reply["ok"], reply["code"]) == (False, "rate_limited")
    assert (await _log(client))[0]["outcome"] == "rate_limited"
    assert model.calls == 20


async def test_a_failed_model_call_answers_with_a_stable_code_and_frees_the_slot(
    client: httpx.AsyncClient, model: _Model, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _install_and_allow(client)
    failing = _Model()
    failing.ainvoke = AsyncMock(side_effect=RuntimeError("provider down: key sk-123"))  # type: ignore[method-assign]
    monkeypatch.setattr(router_module, "get_llm_for_user", AsyncMock(return_value=failing))

    reply = await _ask(client)

    assert (reply["ok"], reply["code"], reply["message"]) == (False, "upstream_error", "The model call failed")
    assert "sk-123" not in str(await _log(client))
    assert AI_LIMITER._in_flight["clock"] == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"prompt": ""},
        {"prompt": "x" * (ai_proxy.MAX_PROMPT_CHARS + 1)},
        {"system": "x" * (ai_proxy.MAX_SYSTEM_CHARS + 1)},
        {"max_tokens": 0},
        {"max_tokens": ai_proxy.MAX_OUTPUT_TOKENS + 1},
        {"version_sha256": "short"},
    ],
)
async def test_a_malformed_request_is_refused_before_anything_runs(
    client: httpx.AsyncClient, model: _Model, overrides: dict[str, object]
) -> None:
    await _install_and_allow(client)

    reply = await client.post(_AI, json={**_PAGE_REQUEST, **overrides})

    assert reply.status_code == 422
    assert model.calls == 0


async def test_the_default_output_cap_applies_when_the_page_names_none(client: httpx.AsyncClient, model: _Model) -> None:
    await _install_and_allow(client)
    seen: list[int] = []

    async def ainvoke(input: list[BaseMessage], *, max_tokens: int) -> AIMessage:
        seen.append(max_tokens)
        return AIMessage(content="ok")

    model.ainvoke = ainvoke  # type: ignore[method-assign]

    await client.post(_AI, json={"version_sha256": _SHA, "prompt": "hi"})

    assert seen == [ai_proxy.DEFAULT_OUTPUT_TOKENS]


async def test_the_ai_grant_takes_no_scope(client: httpx.AsyncClient) -> None:
    await client.put("/api/v1/artifact-apps/clock")

    reply = await client.put(
        "/api/v1/artifact-apps/clock/grants/ai", json={"decision": "allow", "scope": ["x"], "version_sha256": _SHA}
    )

    assert reply.status_code == 422
