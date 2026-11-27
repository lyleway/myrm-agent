"""Tests for ``FetchEngineEscalationMixin``: L4 remote escalation and the bilibili cookie loader."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.escalation.context import bind_web_fetch_escalation_context
from myrm_agent_harness.toolkits.web_fetch.escalation.protocols import EscalationFetchResult
from tests.toolkits.web_fetch._degradation_support import (
    StubProvider,
    http_result,
    make_document,
    mock_pipeline,
    stub_fetchers,
)


class TestEscalationMixin:
    @pytest.mark.asyncio
    async def test_escalation_no_providers(self) -> None:
        engine = FetchEngine()

        doc, result = await engine._try_escalation("http://example.com/page")

        assert doc is None
        assert result is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_escalation_provider_failure_then_success(self) -> None:
        engine = FetchEngine()
        engine._pipeline = mock_pipeline()  # type: ignore[assignment]
        engine._pipeline.aprocess.return_value = make_document()
        good = StubProvider(
            "reader2", EscalationFetchResult(url="", content="# final", title="t", provider_id="reader2")
        )
        bad = StubProvider("broken", error=RuntimeError("boom"))

        with bind_web_fetch_escalation_context(providers=[bad, good]):
            doc, result = await engine._try_escalation("http://example.com/page")

        assert doc is not None
        assert doc.page_content == "# final"
        assert result is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_escalation_html_content_pipeline(self) -> None:
        engine = FetchEngine()
        engine._pipeline = mock_pipeline()  # type: ignore[assignment]
        engine._pipeline.aprocess.return_value = make_document("html doc")
        provider = StubProvider(
            "reader4",
            EscalationFetchResult(
                url="http://example.com/page",
                content="<p>html</p>",
                title="t",
                provider_id="reader4",
                is_markdown=False,
            ),
        )

        with bind_web_fetch_escalation_context(providers=[provider]):
            doc, _result = await engine._try_escalation("http://example.com/page")

        assert doc is not None
        assert doc.metadata.get("escalation_provider") == "reader4"
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_escalation_empty_content_skipped(self) -> None:
        engine = FetchEngine()
        provider = StubProvider("reader5", EscalationFetchResult(url="", content="   ", provider_id="reader5"))

        with bind_web_fetch_escalation_context(providers=[provider]):
            doc, result = await engine._try_escalation("http://example.com/page")

        assert doc is None
        assert result is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_load_bilibili_cookies_no_vault(self) -> None:
        engine = FetchEngine()
        engine._http_fetcher._session_vault = None

        cookies = await engine._load_bilibili_cookies()

        assert cookies is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_load_bilibili_cookies_with_entry(self) -> None:
        engine = FetchEngine()
        vault = AsyncMock()
        vault.load.return_value = SimpleNamespace(
            storage_state={
                "cookies": [
                    {"name": "SESSDATA", "value": "v1"},
                    {"name": "buvid3", "value": "v2"},
                ]
            }
        )
        engine._http_fetcher._session_vault = vault

        cookies = await engine._load_bilibili_cookies()

        assert cookies == {"SESSDATA": "v1", "buvid3": "v2"}
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_load_bilibili_cookies_missing_storage_state(self) -> None:
        engine = FetchEngine()
        vault = AsyncMock()
        vault.load.return_value = SimpleNamespace(storage_state=None)
        engine._http_fetcher._session_vault = vault

        cookies = await engine._load_bilibili_cookies()

        assert cookies is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_load_bilibili_cookies_vault_error(self) -> None:
        engine = FetchEngine()
        vault = AsyncMock()
        vault.load.side_effect = RuntimeError("vault error")
        engine._http_fetcher._session_vault = vault

        cookies = await engine._load_bilibili_cookies()

        assert cookies is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_escalation_html_pipeline_returns_none_continues(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result("", status=404)
        provider = StubProvider(
            "p1", EscalationFetchResult(url="", content="<p>raw html</p>", provider_id="p1", is_markdown=False)
        )
        engine._pipeline = mock_pipeline()  # type: ignore[assignment]
        engine._pipeline.aprocess.return_value = None

        with bind_web_fetch_escalation_context(providers=[provider]):
            doc, result = await engine._crawl_with_degradation("http://example.com/page")

        assert doc is None
        assert result is None
        await engine.shutdown()
