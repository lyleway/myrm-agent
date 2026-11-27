"""Tests for FetchEngine L4 escalation hook."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.escalation.context import bind_web_fetch_escalation_context
from myrm_agent_harness.toolkits.web_fetch.escalation.protocols import (
    EscalationFetchResult,
    FetchEscalationProvider,
)


class _StubProvider:
    provider_id = "stub"

    def __init__(self, result: EscalationFetchResult | None) -> None:
        self._result = result
        self.fetch_url = AsyncMock(return_value=result)


@pytest.mark.asyncio
async def test_try_escalation_returns_document_when_provider_succeeds() -> None:
    engine = FetchEngine()
    provider = _StubProvider(
        EscalationFetchResult(
            url="https://example.com",
            content="# Title\n\nBody text",
            title="Title",
            provider_id="stub",
        )
    )

    with bind_web_fetch_escalation_context(providers=[provider]):
        doc, fetch_result = await engine._try_escalation("https://example.com")

    assert doc is not None
    assert "Body text" in doc.page_content
    assert doc.metadata.get("escalation_provider") == "stub"
    assert fetch_result is None
    provider.fetch_url.assert_awaited_once()


@pytest.mark.asyncio
async def test_crawl_with_degradation_skips_escalation_when_disabled() -> None:
    engine = FetchEngine()
    provider = _StubProvider(EscalationFetchResult(url="https://example.com", content="remote", provider_id="stub"))

    with (
        bind_web_fetch_escalation_context(providers=[provider]),
        patch.object(engine, "_try_and_report", new=AsyncMock(return_value=(None, True, None))),
    ):
        doc, _ = await engine._crawl_with_degradation("https://example.com", allow_escalation=False)

    assert doc is None
    provider.fetch_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_try_escalation_logs_when_no_provider_has_usable_content(caplog: pytest.LogCaptureFixture) -> None:
    engine = FetchEngine()
    providers: list[FetchEscalationProvider] = [_StubProvider(None), _StubProvider(None)]

    with caplog.at_level(logging.INFO), bind_web_fetch_escalation_context(providers=providers):
        doc, _ = await engine._try_escalation("https://example.com")

    assert doc is None
    assert "Escalation found no usable content for https://example.com (2 provider(s) tried)" in caplog.text


@pytest.mark.asyncio
async def test_crawl_with_degradation_calls_escalation_after_l3_failure() -> None:
    engine = FetchEngine()
    provider = _StubProvider(
        EscalationFetchResult(url="https://example.com", content="remote body", provider_id="stub")
    )

    with (
        bind_web_fetch_escalation_context(providers=[provider]),
        patch.object(engine, "_try_and_report", new=AsyncMock(return_value=(None, True, None))),
    ):
        doc, _ = await engine._crawl_with_degradation("https://example.com", allow_escalation=True)

    assert doc is not None
    assert doc.page_content == "remote body"
    provider.fetch_url.assert_awaited_once()
