"""Tests for ``FetchEngine._crawl_with_degradation`` (L1 HTTP + optional L4 remote escalation)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.escalation.context import bind_web_fetch_escalation_context
from myrm_agent_harness.toolkits.web_fetch.escalation.protocols import (
    EscalationFetchResult,
    FetchEscalationProvider,
)
from myrm_agent_harness.toolkits.web_fetch.fetchers.protocols import FetcherType
from tests.toolkits.web_fetch._degradation_support import (
    RICH_HTML,
    StubProvider,
    http_result,
    make_document,
    pipeline_double,
    stub_fetchers,
)


class TestCrawlWithDegradation:
    @pytest.mark.asyncio
    async def test_http_success_no_escalation(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result(RICH_HTML)
        pipeline_double(engine).aprocess.return_value = make_document()

        doc, _result = await engine._crawl_with_degradation("http://example.com/page")

        assert doc is not None
        http.fetch.assert_awaited_once()
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_http_4xx_not_degradable_returns(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result("", status=404)

        doc, _result = await engine._crawl_with_degradation("http://example.com/page")

        assert doc is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_http_failure_without_escalation(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = None

        doc, result = await engine._crawl_with_degradation("http://example.com/page", allow_escalation=False)

        assert doc is None
        assert result is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_http_failure_escalates_to_l4(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = None
        provider = StubProvider("esc2", EscalationFetchResult(url="", content="# Last resort", provider_id="esc2"))

        with bind_web_fetch_escalation_context(providers=[provider]):
            doc, _result = await engine._crawl_with_degradation("http://example.com/page")

        assert doc is not None
        assert doc.page_content == "# Last resort"
        assert doc.metadata["escalation_provider"] == "esc2"
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_escalation_html_pipeline_none_then_next_provider(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = None
        pipeline_double(engine).aprocess.side_effect = [None, make_document("html escaped")]
        providers: list[FetchEscalationProvider] = [
            StubProvider(
                "p1", EscalationFetchResult(url="", content="<p>raw one</p>", provider_id="p1", is_markdown=False)
            ),
            StubProvider(
                "p2", EscalationFetchResult(url="", content="<p>raw two</p>", provider_id="p2", is_markdown=False)
            ),
        ]

        with bind_web_fetch_escalation_context(providers=providers):
            doc, _result = await engine._crawl_with_degradation("http://example.com/page")

        assert doc is not None
        assert doc.page_content == "html escaped"
        assert doc.metadata["escalation_provider"] == "p2"
        await engine.shutdown()


class TestLadderRemainingBranches:
    @pytest.mark.asyncio
    async def test_weixin_no_js_content_returns_none_degrades(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result(RICH_HTML)
        url = "https://mp.weixin.qq.com/s/abc123"
        with (
            patch(
                "myrm_agent_harness.toolkits.web_fetch.extractors.weixin_extractor.parse_weixin_article_html",
                new=lambda html, url=None: None,
            ),
            patch(
                "myrm_agent_harness.toolkits.web_fetch.extractors.weixin_extractor.has_weixin_js_content",
                new=lambda html: False,
            ),
        ):
            doc, degradable, result = await engine._try_fetch_and_process(url, FetcherType.HTTP)

            assert doc is None
            assert degradable is True
            assert result is not None
        await engine.shutdown()
