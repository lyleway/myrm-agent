"""Tests for the cache freshness contract: 15m default TTL, no stale serving, single-flight revalidation, no-store."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.documents import Document

from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.fetchers.protocols import FetcherType, FetchResult


def _result(
    body: str = "content",
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    url: str = "https://example.com",
) -> FetchResult:
    return FetchResult(
        html=f"<html><body>{body}</body></html>" if body else "",
        url=url,
        status_code=status,
        headers=headers or {},
        fetcher_type=FetcherType.HTTP,
    )


def test_default_cache_ttl_is_15_minutes() -> None:
    engine = FetchEngine(allow_private_networks=True)
    assert engine._crawl_cache.ttl == 900


@pytest.mark.asyncio
async def test_expired_cache_synchronously_revalidates_and_returns_fresh_content() -> None:
    engine = FetchEngine(cache_ttl=1, allow_private_networks=True)
    first_doc = Document(page_content="old content", metadata={})
    second_doc = Document(page_content="new content", metadata={})

    with patch.object(engine, "_crawl_with_degradation", new_callable=AsyncMock) as mock_crawl:
        mock_crawl.side_effect = [
            (first_doc, _result("old content", headers={"etag": "abc123"})),
            (second_doc, _result("new content", headers={"etag": "def456"})),
        ]

        doc1 = await engine.crawl("https://example.com")
        assert doc1 is not None and doc1.page_content == "old content"
        assert mock_crawl.call_count == 1

        await asyncio.sleep(1.1)

        doc2 = await engine.crawl("https://example.com")
        assert doc2 is not None and doc2.page_content == "new content"
        assert mock_crawl.call_count == 2
        assert mock_crawl.call_args_list[1].kwargs["etag"] == "abc123"

    await engine.shutdown()


@pytest.mark.asyncio
async def test_expired_cache_304_refreshes_freshness_window() -> None:
    engine = FetchEngine(cache_ttl=1, allow_private_networks=True)
    first_doc = Document(page_content="content", metadata={})

    with patch.object(engine, "_crawl_with_degradation", new_callable=AsyncMock) as mock_crawl:
        mock_crawl.side_effect = [
            (first_doc, _result("content", headers={"etag": "abc123"})),
            (None, _result("", status=304, headers={"etag": "abc123"})),
        ]

        assert (await engine.crawl("https://example.com")) is not None
        await asyncio.sleep(1.1)

        doc2 = await engine.crawl("https://example.com")
        assert doc2 is not None and doc2.page_content == "content"
        assert mock_crawl.call_count == 2

        doc3 = await engine.crawl("https://example.com")
        assert doc3 is not None and doc3.page_content == "content"
        assert mock_crawl.call_count == 2

    await engine.shutdown()


@pytest.mark.asyncio
async def test_expired_cache_without_validators_refetches_instead_of_serving_stale() -> None:
    engine = FetchEngine(cache_ttl=1, allow_private_networks=True)
    first_doc = Document(page_content="v1", metadata={})
    second_doc = Document(page_content="v2", metadata={})

    with patch.object(engine, "_crawl_with_degradation", new_callable=AsyncMock) as mock_crawl:
        mock_crawl.side_effect = [(first_doc, _result("v1")), (second_doc, _result("v2"))]

        doc1 = await engine.crawl("https://example.com")
        assert doc1 is not None and doc1.page_content == "v1"

        await asyncio.sleep(1.1)

        doc2 = await engine.crawl("https://example.com")
        assert doc2 is not None and doc2.page_content == "v2"
        assert mock_crawl.call_count == 2

    await engine.shutdown()


@pytest.mark.asyncio
async def test_failed_revalidation_logs_warning_and_never_serves_stale(caplog: pytest.LogCaptureFixture) -> None:
    engine = FetchEngine(cache_ttl=1, allow_private_networks=True)
    first_doc = Document(page_content="stale", metadata={})

    with (
        caplog.at_level(logging.WARNING, logger="myrm_agent_harness.toolkits.web_fetch.engine.base"),
        patch.object(engine, "_crawl_with_degradation", new_callable=AsyncMock) as mock_crawl,
    ):
        mock_crawl.side_effect = [(first_doc, _result("stale", headers={"etag": "e1"})), (None, None)]

        assert (await engine.crawl("https://example.com")) is not None
        await asyncio.sleep(1.1)

        doc2 = await engine.crawl("https://example.com")
        assert doc2 is None
        assert any("revalidation failed" in record.message.lower() for record in caplog.records)

    await engine.shutdown()


@pytest.mark.asyncio
async def test_concurrent_reads_of_expired_entry_coalesce_to_one_request() -> None:
    engine = FetchEngine(cache_ttl=1, allow_private_networks=True)
    first_doc = Document(page_content="v1", metadata={})
    second_doc = Document(page_content="v2", metadata={})
    gate = asyncio.Event()

    async def slow_refresh(*_args: object, **_kwargs: object) -> tuple[Document, FetchResult]:
        await gate.wait()
        return second_doc, _result("v2", headers={"etag": "e2"})

    with patch.object(engine, "_crawl_with_degradation", new_callable=AsyncMock) as mock_crawl:
        mock_crawl.return_value = (first_doc, _result("v1", headers={"etag": "e1"}))
        assert (await engine.crawl("https://example.com")) is not None

        await asyncio.sleep(1.1)
        mock_crawl.side_effect = slow_refresh

        tasks = [asyncio.create_task(engine.crawl("https://example.com")) for _ in range(10)]
        await asyncio.sleep(0.05)
        gate.set()
        docs = await asyncio.gather(*tasks)

        assert all(d is not None and d.page_content == "v2" for d in docs)
        assert mock_crawl.call_count == 2  # 1 initial + 1 coalesced revalidation (not 10)

    await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Cache-Control": "no-store"},
        {"cache-control": "private, no-store, max-age=0"},
        {"CACHE-CONTROL": "no-cache, NO-STORE"},
    ],
)
async def test_no_store_response_is_never_cached(headers: dict[str, str]) -> None:
    engine = FetchEngine(cache_ttl=900, allow_private_networks=True)
    doc_v1 = Document(page_content="live-1", metadata={})
    doc_v2 = Document(page_content="live-2", metadata={})

    with patch.object(engine, "_crawl_with_degradation", new_callable=AsyncMock) as mock_crawl:
        mock_crawl.side_effect = [
            (doc_v1, _result("live-1", headers=headers)),
            (doc_v2, _result("live-2", headers=headers)),
        ]

        first = await engine.crawl("https://example.com/private")
        second = await engine.crawl("https://example.com/private")

        assert first is not None and first.page_content == "live-1"
        assert second is not None and second.page_content == "live-2"
        assert mock_crawl.call_count == 2

    await engine.shutdown()


def test_cache_metrics_no_longer_expose_background_revalidation() -> None:
    engine = FetchEngine(allow_private_networks=True)
    metrics = engine.get_cache_metrics()
    assert set(metrics.keys()) == {"crawl_cache", "fail_cache"}
