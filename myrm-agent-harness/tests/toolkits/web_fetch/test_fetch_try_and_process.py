"""Tests for ``FetchEngine._try_fetch_and_process`` result handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.fetchers.protocols import (
    FetcherType,
    FetchResult,
)
from tests.toolkits.web_fetch._degradation_support import (
    RICH_HTML,
    http_result,
    make_document,
    stub_fetchers,
)


class TestTryFetchAndProcess:
    @pytest.mark.asyncio
    async def test_http_304_not_modified(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result("", status=304)

        doc, degradable, result = await engine._try_fetch_and_process(
            "http://example.com/page", FetcherType.HTTP, etag="abc"
        )

        assert doc is None
        assert degradable is False
        assert result is not None and result.status_code == 304
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_http_4xx_non_degradable_aborts(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result("", status=404)

        doc, degradable, result = await engine._try_fetch_and_process("http://example.com/page", FetcherType.HTTP)

        assert doc is None
        assert degradable is False
        assert result is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_http_empty_shell_degrades(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result("<html><head></head></html>")

        doc, degradable, _ = await engine._try_fetch_and_process("http://example.com/page", FetcherType.HTTP)

        assert doc is None
        assert degradable is True
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_fetcher_none_result(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = None

        doc, degradable, result = await engine._try_fetch_and_process("http://example.com/page", FetcherType.HTTP)

        assert doc is None
        assert degradable is True
        assert result is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_binary_body_routed(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = FetchResult(
            html=RICH_HTML,
            url="http://example.com/img.png",
            raw_body=b"\x89PNG\r\n",
            headers={"content-type": "image/png"},
        )
        with patch(
            "myrm_agent_harness.toolkits.web_fetch.processing.binary_router.route_binary_content",
            new=AsyncMock(return_value=make_document("binary")),
        ):
            doc, degradable, result = await engine._try_fetch_and_process(
                "http://example.com/img.png", FetcherType.HTTP
            )

            assert doc is not None
            assert doc.page_content == "binary"
            assert degradable is False
            assert result is not None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_weixin_url_parsed_fastpath(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result(RICH_HTML)
        url = "https://mp.weixin.qq.com/s/abc123"
        with patch(
            "myrm_agent_harness.toolkits.web_fetch.extractors.weixin_extractor.parse_weixin_article_html",
            new=lambda html, url=None: make_document("weixin"),
        ):
            doc, degradable, _ = await engine._try_fetch_and_process(url, FetcherType.HTTP)

            assert doc is not None
            assert doc.page_content == "weixin"
            assert degradable is False
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_weixin_without_js_content_degrades(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result("<html>shell</html>")
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
            doc, degradable, _ = await engine._try_fetch_and_process(url, FetcherType.HTTP)

            assert doc is None
            assert degradable is True
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_antibot_detected_degrades(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result(RICH_HTML, status=503)
        with patch(
            "myrm_agent_harness.toolkits.web_fetch.engine.fetch_mixin.classify_block",
            new=lambda status, html, **kwargs: ("blocked", "challenge"),
        ):
            doc, degradable, result = await engine._try_fetch_and_process("http://example.com/page", FetcherType.HTTP)

            assert doc is None
            assert degradable is True
            assert result is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_pipeline_doc_is_returned(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result(RICH_HTML)
        converted = make_document("converted content")
        engine._pipeline.aprocess.return_value = converted

        doc, degradable, result = await engine._try_fetch_and_process("http://example.com/page", FetcherType.HTTP)

        assert doc is converted
        assert degradable is True
        assert result is not None
        await engine.shutdown()


class TestFetchMixinRemaining:
    @pytest.mark.asyncio
    async def test_weixin_with_js_content_continues_generic(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = http_result(RICH_HTML)
        engine._pipeline.aprocess.return_value = make_document("generic")
        with (
            patch(
                "myrm_agent_harness.toolkits.web_fetch.extractors.weixin_extractor.parse_weixin_article_html",
                new=lambda html, url=None: None,
            ),
            patch(
                "myrm_agent_harness.toolkits.web_fetch.extractors.weixin_extractor.has_weixin_js_content",
                new=lambda html: True,
            ),
        ):
            doc, degradable, _result = await engine._try_fetch_and_process(
                "https://mp.weixin.qq.com/s/abc123", FetcherType.HTTP
            )

            assert doc is not None
            assert doc.page_content == "generic"
            assert degradable is True
        await engine.shutdown()
