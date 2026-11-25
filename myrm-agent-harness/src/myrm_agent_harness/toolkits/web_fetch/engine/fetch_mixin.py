"""FetchEngine network fetch, platform fast-path, L1 HTTP / L4 escalation, and failure-history helpers.

[INPUT]
web_fetch.extractors.bilibili_extractor::extract_bilibili_subtitle, is_bilibili_url (POS: Bilibili subtitle fast-path)
web_fetch.extractors.weixin_extractor::extract_weixin_article, is_weixin_article_url (POS: WeChat article fast-path)
web_fetch.extractors.x_extractor::extract_x_post, is_x_url (POS: X post fast-path)
web_fetch.extractors.youtube_extractor::extract_youtube_transcript, is_youtube_url (POS: YouTube transcript fast-path)
web_fetch.fetchers.protocols::FetcherType, FetchResult (POS: Fetcher protocol types)
web_fetch.processing.antibot_detector::classify_block (POS: Anti-bot, JS-shell, and error-page classification)
web_fetch.processing.pipeline::run_on_conversion_thread (POS: Dedicated conversion thread runner)
web_fetch.router::domain_key (POS: Domain normalization key)
web_fetch.engine.types::DEGRADABLE_4XX (POS: Shared engine types and constants)

[OUTPUT]
FetchEngineFetchMixin: Network fetch, platform fast-path, L1 HTTP with optional L4 escalation, and failure-history feedback mixin

[POS]
Mixin: platform fast-paths (YouTube, Bilibili, WeChat, X) ahead of L1 HTTP fetch + pipeline, optional L4 remote escalation, and failure-history feedback.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from langchain_core.documents import Document

from myrm_agent_harness.utils.content_sniff import PayloadKind

from ..extractors.bilibili_extractor import extract_bilibili_subtitle, is_bilibili_url
from ..extractors.weixin_extractor import extract_weixin_article, is_weixin_article_url
from ..extractors.x_extractor import extract_x_post, is_x_url
from ..extractors.youtube_extractor import extract_youtube_transcript, is_youtube_url
from ..fetchers.protocols import FetcherType, FetchResult
from ..processing.antibot_detector import classify_block
from ..router import domain_key
from .types import DEGRADABLE_4XX

if TYPE_CHECKING:
    from ..fetchers.http_fetcher import HttpFetcher
    from ..processing.pipeline import ContentPipeline
    from ..router.domain_metrics import DomainMetricsManager
    from .types import FailureKind

logger = logging.getLogger(__name__)


class FetchEngineFetchMixin:
    if TYPE_CHECKING:
        _http_fetcher: HttpFetcher
        _pipeline: ContentPipeline
        _domain_metrics: DomainMetricsManager
        _crawl_timeout: float
        _youtube_languages: list[str] | None

        def _record_failure_reason(self, url: str, kind: FailureKind, reason: str) -> None: ...

        async def _try_escalation(self, url: str) -> tuple[Document | None, FetchResult | None]: ...

        async def _load_bilibili_cookies(self) -> dict[str, str] | None: ...

    async def _try_fetch_and_process(
        self,
        url: str,
        fetcher_type: FetcherType = FetcherType.HTTP,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> tuple[Document | None, bool, FetchResult | None]:
        """Fetch with HTTP fetcher and process through the pipeline."""
        if etag or last_modified:
            fetch_result: FetchResult | None = await self._http_fetcher.fetch(
                url, etag=etag, last_modified=last_modified
            )
        else:
            fetch_result = await self._http_fetcher.fetch(url)

        if fetch_result is None:
            return None, True, None

        if fetch_result.status_code == 304:
            logger.info(f"HTTP 304 Not Modified: {url}")
            return None, False, fetch_result

        if 400 <= fetch_result.status_code < 500 and fetch_result.status_code not in DEGRADABLE_4XX:
            failure_kind: FailureKind = "not_found" if fetch_result.status_code in (404, 410) else "error_page"
            self._record_failure_reason(url, failure_kind, f"HTTP {fetch_result.status_code}")
            logger.warning(f"HTTP {fetch_result.status_code} not degradable, abort: {url}")
            return None, False, None

        # Files (PDF, Office, images) reach here as raw bytes with empty html, so they are routed to a file parser
        # before the empty-shell check.
        if fetch_result.raw_body is not None:
            from ..processing.binary_router import route_binary_content

            doc = await route_binary_content(fetch_result.raw_body, fetch_result.headers, url)
            if doc is not None or fetch_result.status_code < 400:
                return doc, False, fetch_result

        # Text (plain, markdown, csv, feeds, source files) is already the content; the HTML-only checks below
        # (empty shell, anti-bot) would reject it for lacking markup.
        if fetch_result.kind is PayloadKind.TEXT and fetch_result.status_code < 400:
            return await self._pipeline.aprocess(fetch_result), True, fetch_result

        if not fetch_result.has_content:
            kind, reason = classify_block(fetch_result.status_code, fetch_result.html, headers=fetch_result.headers)
            if kind is not None:
                self._record_failure_reason(url, kind, reason)
                logger.warning("Fetch %s (%s): %s", kind, reason, url)
            else:
                self._record_failure_reason(url, "needs_javascript", "L1 HTTP returned empty shell")
                logger.warning("L1 HTTP returned empty shell: %s", url)
            return None, True, None

        from ..extractors.weixin_extractor import has_weixin_js_content, parse_weixin_article_html
        from ..processing.pipeline import run_on_conversion_thread

        if is_weixin_article_url(url):
            weixin_doc = await run_on_conversion_thread(parse_weixin_article_html, fetch_result.html, url=url)
            if weixin_doc is not None:
                return weixin_doc, False, fetch_result
            if not has_weixin_js_content(fetch_result.html):
                logger.warning(
                    "Weixin URL missing js_content, skipping generic HTML extraction: %s",
                    url,
                )
                return None, True, fetch_result

        kind, reason = classify_block(fetch_result.status_code, fetch_result.html, headers=fetch_result.headers)
        if kind is not None:
            self._record_failure_reason(url, kind, reason)
            logger.warning("Anti-bot detected [%s] (%s): %s", kind, reason, url)
            return None, True, None

        return await self._pipeline.aprocess(fetch_result), True, fetch_result

    async def _try_and_report(
        self,
        url: str,
        fetcher_type: FetcherType = FetcherType.HTTP,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> tuple[Document | None, bool, FetchResult | None]:
        """Attempt HTTP fetch and record failures in domain metrics."""
        doc, degradable, fetch_result = await self._try_fetch_and_process(
            url,
            fetcher_type,
            etag=etag,
            last_modified=last_modified,
        )
        if doc is not None or degradable:
            self._report_feedback(url, fetcher_type, success=(doc is not None))
        return doc, degradable, fetch_result

    async def _crawl_with_degradation(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        allow_escalation: bool = True,
    ) -> tuple[Document | None, FetchResult | None]:
        """Fetch via L1 HTTP, escalating to optional L4 remote providers when configured."""
        doc, degradable, result = await self._try_and_report(
            url,
            FetcherType.HTTP,
            etag=etag,
            last_modified=last_modified,
        )
        if doc:
            return doc, result
        if not degradable:
            if allow_escalation and result is not None and result.kind is PayloadKind.BINARY:
                esc_doc, esc_result = await self._try_escalation(url)
                if esc_doc:
                    return esc_doc, esc_result
            return doc, result

        if allow_escalation:
            esc_doc, esc_result = await self._try_escalation(url)
            if esc_doc:
                return esc_doc, esc_result

        return doc, result

    def _report_feedback(self, url: str, fetcher_type: FetcherType, *, success: bool) -> None:
        """Record fetch failures in domain metrics for the site-experience staleness hint."""
        if not success:
            self._domain_metrics.record_failure(domain_key(url), fetcher_type)

    async def _fetch_from_network(
        self,
        url: str,
        *,
        etag: str | None,
        last_modified: str | None,
        allow_escalation: bool,
    ) -> tuple[Document | None, FetchResult | None]:
        """Fetch ``url`` over the network: the platform fast-path when it has one, then L1 HTTP / optional L4.

        Each stage runs under its own crawl timeout, so a URL whose fast-path misses can take twice the cap.
        """

        async def degrade() -> tuple[Document | None, FetchResult | None]:
            async with asyncio.timeout(self._crawl_timeout):
                return await self._crawl_with_degradation(
                    url,
                    etag=etag,
                    last_modified=last_modified,
                    allow_escalation=allow_escalation,
                )

        proxy_pool = self._http_fetcher._proxy_pool
        if is_youtube_url(url):
            async with asyncio.timeout(self._crawl_timeout):
                doc = await extract_youtube_transcript(
                    url, preferred_languages=self._youtube_languages, proxy_pool=proxy_pool
                )
            return (doc, None) if doc is not None else await degrade()

        if is_bilibili_url(url):
            async with asyncio.timeout(self._crawl_timeout):
                cookies = await self._load_bilibili_cookies()
                doc = await extract_bilibili_subtitle(url, cookies=cookies, proxy_pool=proxy_pool)
            return (doc, None) if doc is not None else await degrade()

        if is_weixin_article_url(url):
            async with asyncio.timeout(self._crawl_timeout):
                doc = await extract_weixin_article(url, proxy_pool=proxy_pool)
            if doc is not None:
                logger.info("Weixin article fast-path succeeded: %s", url)
                return doc, None
            logger.info("Weixin article fast-path missed, falling back to HTTP/L4: %s", url)
            doc, fetch_result = await degrade()
            if doc is not None:
                logger.info("Weixin article fallback fetch succeeded: %s", url)
            else:
                logger.warning("Weixin article fetch failed after degradation: %s", url)
            return doc, fetch_result

        if is_x_url(url):
            async with asyncio.timeout(self._crawl_timeout):
                doc = await extract_x_post(url, proxy_pool=proxy_pool)
            if doc is not None:
                logger.info("Twitter/X post fast-path succeeded: %s", url)
                return doc, None
            logger.info("Twitter/X post fast-path missed, degrading to standard fetch: %s", url)

        return await degrade()
