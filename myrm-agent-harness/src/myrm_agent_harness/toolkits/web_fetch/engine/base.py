"""FetchEngine — HTTP fetch engine with optional L4 remote escalation.

Architecture:
  L1 HttpFetcher (curl_cffi, ~100 ms)
  Optional L4 remote escalation providers (Jina / Firecrawl / self-hosted compatible service)

Cache: In-memory LRU (default 15 min TTL, 500 entries, 100 MB cap)
  - Request coalescing: concurrent requests for the same URL trigger only one network call (30 s timeout guard; waiters take over a cancelled leader's fetch)
  - URL normalisation: strips known tracking params for better hit rate
  - GitHub blob links are fetched as their raw file, so a long source file is not cut at the page-size ceiling
  - HTTP conditional requests: sends ETag / Last-Modified on cache expiry (304 refreshes freshness; never serves stale)
  - Cache-Control: responses carrying ``no-store`` are never cached
Boundary: single-sandbox, single-process semantics.

[INPUT]
web_fetch.fetchers.http_fetcher::HttpFetcher (POS: L1 HTTP fetcher)
web_fetch.fetchers.target_guard::assert_not_blocklisted (POS: network blocklist verdict for one URL)
web_fetch.fetchers.protocols::FetcherType, FetchResult (POS: Fetcher protocol types)
web_fetch.engine.types::FailureKind (POS: Fetch failure outcome classification literal)
web_fetch.router.domain_metrics::get_global_domain_metrics_manager (POS: Per-domain failure history behind the staleness hint)
browser.pool.proxy::ProxyPool (POS: Proxy pool for HTTP fetcher)
utils.lru_cache::LRUCache (POS: Generic TTL-based LRU cache)

[OUTPUT]
FetchEngine: HTTP fetch engine with optional L4 remote escalation, caching, and request coalescing

[POS]
Core fetch engine. Orchestrates L1 HTTP fetching and optional L4 remote escalation with
freshness-only in-memory caching, conditional revalidation, and request coalescing.
The network blocklist is fixed for the life of an engine, so its caches never hold a page fetched under another
list; a caller whose list can change builds a new engine for each call.

"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from langchain_core.documents import Document

if TYPE_CHECKING:
    from collections.abc import Mapping

    from myrm_agent_harness.toolkits.browser.pool.proxy import ProxyPool
    from myrm_agent_harness.toolkits.browser.session_vault import SessionVault

from myrm_agent_harness.core.security.guards.ssrf import SSRFSecurityError
from myrm_agent_harness.utils.lru_cache import LRUCache

from ..fetchers.http_fetcher import HttpFetcher
from ..fetchers.target_guard import assert_not_blocklisted
from ..processing.pipeline import ContentPipeline
from ..router import get_global_domain_metrics_manager
from .batch_mixin import FetchEngineBatchMixin, cancel_fetches
from .cache_mixin import FetchEngineCacheMixin, await_shared_result
from .escalation_mixin import FetchEngineEscalationMixin
from .fetch_mixin import FetchEngineFetchMixin
from .types import (
    NO_SHARED_RESULT,
    CachedDocument,
    FailedResult,
    InFlightFetch,
    NoSharedResult,
    SuccessResult,
)

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.web_fetch.fetchers.protocols import FetchResult

    from .types import FailureKind

logger = logging.getLogger(__name__)

__all__ = [
    "CachedDocument",
    "FailedResult",
    "FetchEngine",
    "SuccessResult",
]


def _has_no_store(fetch_result: FetchResult | None) -> bool:
    """Return True when the response carries a ``Cache-Control: no-store`` directive."""
    if fetch_result is None or not fetch_result.headers:
        return False
    for key, value in fetch_result.headers.items():
        if key.lower() == "cache-control":
            directives = {part.strip().lower() for part in value.split(",")}
            if "no-store" in directives:
                return True
    return False


class FetchEngine(
    FetchEngineBatchMixin,
    FetchEngineEscalationMixin,
    FetchEngineFetchMixin,
    FetchEngineCacheMixin,
):
    """HTTP fetch engine with optional L4 remote escalation."""

    _DEFAULT_CRAWL_TIMEOUT: float = 45.0

    def __init__(
        self,
        *,
        use_raw_markdown: bool = False,
        proxy_pool: ProxyPool | None = None,
        session_vault: SessionVault | None = None,
        cache_ttl: int = 900,
        cache_maxsize: int = 500,
        cache_max_bytes: int = 100 * 1024 * 1024,
        coalescing_timeout: float = 30.0,
        crawl_timeout: float = _DEFAULT_CRAWL_TIMEOUT,
        allow_private_networks: bool = False,
        network_blocklist: tuple[str, ...] = (),
        youtube_languages: list[str] | None = None,
    ):
        self._allow_private_networks = allow_private_networks
        self._network_blocklist = network_blocklist
        self._youtube_languages = youtube_languages
        self._http_fetcher = HttpFetcher(
            proxy_pool=proxy_pool,
            session_vault=session_vault,
            allow_private_networks=allow_private_networks,
            network_blocklist=network_blocklist,
        )
        self._pipeline = ContentPipeline(use_raw_markdown=use_raw_markdown)

        self._domain_metrics = get_global_domain_metrics_manager()

        def _doc_size(cached: CachedDocument) -> int:
            content_size = len(cached.doc.page_content.encode("utf-8"))
            metadata_size = sum(
                len(str(k).encode("utf-8")) + len(str(v).encode("utf-8")) for k, v in cached.doc.metadata.items()
            )
            return content_size + metadata_size

        self._crawl_cache: LRUCache[CachedDocument] = LRUCache(
            maxsize=cache_maxsize,
            ttl=cache_ttl,
            id="fetch_engine_cache",
            max_bytes=cache_max_bytes,
            size_fn=_doc_size,
        )
        self._fail_cache: LRUCache[bool] = LRUCache(maxsize=200, ttl=300, id="fetch_engine_fail_cache")
        self._failure_reasons: LRUCache[tuple[FailureKind, str]] = LRUCache(
            maxsize=200, ttl=300, id="fetch_engine_failure_reasons"
        )
        self._pending_requests: dict[str, InFlightFetch] = {}
        # Fetches a batch call stopped waiting for: the engine owns them until they end (the loop holds tasks weakly).
        self._detached_fetches: set[asyncio.Task[Document | None]] = set()
        self._enable_http_validation = True
        self._coalescing_timeout = coalescing_timeout
        self._crawl_timeout = crawl_timeout

    def set_session_vault(self, session_vault: SessionVault) -> None:
        """Inject SessionVault dynamically (e.g. from server layer)."""
        self._http_fetcher._session_vault = session_vault

    def get_failure_reason(self, url: str) -> tuple[FailureKind, str] | None:
        """Return the last recorded ``(kind, reason)`` for a failed URL, if any."""
        from ..processing.github_blob_url import github_blob_to_raw
        from ..processing.url_normalizer import normalize_url

        return self._failure_reasons.get(normalize_url(github_blob_to_raw(url)))

    def _record_failure_reason(self, url: str, kind: FailureKind, reason: str) -> None:
        from ..processing.github_blob_url import github_blob_to_raw
        from ..processing.url_normalizer import normalize_url

        self._failure_reasons.set(normalize_url(github_blob_to_raw(url)), (kind, reason))

    async def crawl(
        self,
        url: str,
        *,
        force_refresh: bool = False,
        allow_escalation: bool = True,
        max_parsed_chars: int | None = None,
    ) -> Document | None:
        """Crawl a single URL, return Document or None.

        ``max_parsed_chars`` is where the parsed text of a PDF or Office file is cut (``None``: the router's
        default). The cached page and the fetch in flight are kept per cap, so a caller never gets text cut at
        another caller's length; what is remembered of a failure is per URL.
        """
        from ..processing.github_blob_url import github_blob_to_raw
        from ..processing.url_normalizer import normalize_url

        # A pure string check, so it runs ahead of the SSRF lookup, which may resolve the name.
        try:
            assert_not_blocklisted(url, self._network_blocklist)
        except SSRFSecurityError as exc:
            logger.warning(f"URL blocked by outbound policy: {url} — {exc}")
            return None

        if not self._allow_private_networks:
            from myrm_agent_harness.core.security.guards.ssrf import async_validate_url_for_ssrf

            ssrf_check = await async_validate_url_for_ssrf(url)
            if not ssrf_check.safe:
                logger.warning(f"URL blocked (SSRF): {url} — {ssrf_check.error}")
                return None

        # The policy checks above judged the URL as given; the rewrite stays on the same host, and the redirect it
        # leads to is judged hop by hop in the fetch. A blob link then shares the cache entry of its raw link.
        url = github_blob_to_raw(url)
        url_key = normalize_url(url)
        # normalize_url rebuilds the URL from its parsed parts and urlparse drops every newline, so no URL can spell
        # the key of a capped entry.
        cache_key = url_key if max_parsed_chars is None else f"{url_key}\n{max_parsed_chars}"

        # A leader that ended without a result (cancelled) sends its waiters round again: the first becomes the
        # new leader and the rest coalesce on it, so one fetch still serves them all.
        while not force_refresh and cache_key in self._pending_requests:
            logger.info(f"Request coalescing: waiting for in-flight request: {url}")
            in_flight = self._pending_requests[cache_key]
            shared = await await_shared_result(in_flight, url=url, timeout_s=self._coalescing_timeout)
            if not isinstance(shared, NoSharedResult):
                return shared
            if self._pending_requests.get(cache_key) is in_flight:
                del self._pending_requests[cache_key]  # stalled leader: stop sending newcomers to wait on it

        if force_refresh:
            self._crawl_cache.delete(cache_key)
            self._fail_cache.delete(url_key)
            self._failure_reasons.delete(url_key)

        cached_item, is_expired = self._crawl_cache.get_with_expiry(cache_key)
        if cached_item is not None and not force_refresh:
            result = self._handle_cache_hit(cached_item, is_expired, url, cache_key)
            if result is not None:
                return result

        if self._fail_cache.contains(url_key):
            logger.info(f"Fail cache hit (skipping): {url}")
            return None

        future: InFlightFetch = asyncio.Future()
        self._pending_requests[cache_key] = future

        etag = cached_item.etag if cached_item and self._enable_http_validation else None
        last_modified = cached_item.last_modified if cached_item and self._enable_http_validation else None

        try:
            doc, fetch_result = await self._fetch_from_network(
                url,
                etag=etag,
                last_modified=last_modified,
                allow_escalation=allow_escalation,
                max_parsed_chars=max_parsed_chars,
            )

            if fetch_result and fetch_result.status_code == 304 and cached_item:
                logger.info(f"HTTP 304: cache still valid: {url}")
                if _has_no_store(fetch_result):
                    self._crawl_cache.delete(cache_key)
                else:
                    cached_item.cached_at = time.time()
                    self._crawl_cache.set(cache_key, cached_item)
                doc = cached_item.doc
            elif doc is not None:
                if _has_no_store(fetch_result):
                    self._crawl_cache.delete(cache_key)
                else:
                    new_etag = fetch_result.etag if fetch_result else None
                    new_last_modified = fetch_result.last_modified if fetch_result else None
                    cached = CachedDocument(
                        doc=doc,
                        etag=new_etag,
                        last_modified=new_last_modified,
                        cached_at=time.time(),
                    )
                    self._crawl_cache.set(cache_key, cached)
            else:
                if cached_item is not None:
                    logger.warning("Cache revalidation failed; not serving stale entry: %s", url)
                    self._crawl_cache.delete(cache_key)
                self._fail_cache.set(url_key, True)

            if not future.done():
                future.set_result(doc)

            return doc
        except Exception as e:
            return await self._handle_fetch_error(url, url_key, future, e)
        finally:
            self._pending_requests.pop(cache_key, None)
            if not future.done():  # cancelled mid-fetch: let the waiters fetch for themselves, not wait out the timeout
                future.set_result(NO_SHARED_RESULT)

    async def shutdown(self) -> None:
        await cancel_fetches(list(self._detached_fetches))

        await self._http_fetcher.shutdown()
        await self._domain_metrics.shutdown_async()

        from ..router.site_experience import get_global_site_experience_store

        get_global_site_experience_store().shutdown()

    def get_cache_metrics(self) -> dict[str, Mapping[str, object]]:
        """Get cache metrics (for monitoring and tuning)."""
        return {
            "crawl_cache": self._crawl_cache.get_metrics(),
            "fail_cache": self._fail_cache.get_metrics(),
        }
