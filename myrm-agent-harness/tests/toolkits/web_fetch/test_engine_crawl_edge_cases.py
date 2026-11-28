"""Tests for FetchEngine crawl edge cases.

Covers SSRF rejection in ``crawl``, bilibili / weixin
fast-path degradation, and ``crawl_many`` failure paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document

from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine


def _doc(text: str = "content") -> Document:
    return Document(page_content=text, metadata={"url": "http://example.com/page"})


BILIBILI_URL = "https://www.bilibili.com/video/BV1xx411c7mD"
WEIXIN_URL = "https://mp.weixin.qq.com/s/abc123"
PLAIN_URL = "http://example.com/page"


# ===================================================================
# base.py — crawl entry guards and fast-path branches
# ===================================================================


class TestCrawlGuardsAndFastPaths:
    @pytest.mark.asyncio
    async def test_crawl_ssrf_blocked(self) -> None:
        engine = FetchEngine()

        doc = await engine.crawl("http://192.168.1.1/admin")

        assert doc is None
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_crawl_bilibili_fastpath_success(self) -> None:
        engine = FetchEngine()
        engine._allow_private_networks = True
        engine._http_fetcher._session_vault = None

        with patch(
            "myrm_agent_harness.toolkits.web_fetch.engine.fetch_mixin.extract_bilibili_subtitle",
            new=AsyncMock(return_value=_doc("bilibili transcript")),
        ):
            doc = await engine.crawl(BILIBILI_URL)

            assert doc is not None
            assert doc.page_content == "bilibili transcript"
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_crawl_bilibili_fastpath_miss_degrades(self) -> None:
        engine = FetchEngine()
        engine._allow_private_networks = True
        degraded = _doc("degraded")
        with (
            patch(
                "myrm_agent_harness.toolkits.web_fetch.engine.fetch_mixin.extract_bilibili_subtitle",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                engine,
                "_crawl_with_degradation",
                new=AsyncMock(return_value=(degraded, None)),
            ),
        ):
            doc = await engine.crawl(BILIBILI_URL)

            assert doc is not None
            assert doc.page_content == "degraded"
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_crawl_weixin_fastpath_miss_degrades(self) -> None:
        engine = FetchEngine()
        engine._allow_private_networks = True
        degraded = _doc("weixin degraded")
        with (
            patch(
                "myrm_agent_harness.toolkits.web_fetch.engine.fetch_mixin.extract_weixin_article",
                new=AsyncMock(return_value=None),
            ) as fast_path,
            patch.object(
                engine,
                "_crawl_with_degradation",
                new=AsyncMock(return_value=(degraded, None)),
            ),
        ):
            doc = await engine.crawl(WEIXIN_URL)

            assert doc is not None
            assert doc.page_content == "weixin degraded"
            fast_path.assert_awaited_once()
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_set_session_vault(self) -> None:
        engine = FetchEngine()
        vault = MagicMock()

        engine.set_session_vault(vault)

        assert engine._http_fetcher._session_vault is vault
        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_crawl_many_reports_failures(self) -> None:
        engine = FetchEngine()
        engine.crawl = AsyncMock(return_value=None)  # type: ignore[method-assign]

        success, failed = await engine.crawl_many(["http://a.example/x", "http://b.example/x"], max_concurrency=2)

        assert success == []
        assert len(failed) == 2
        await engine.shutdown()
