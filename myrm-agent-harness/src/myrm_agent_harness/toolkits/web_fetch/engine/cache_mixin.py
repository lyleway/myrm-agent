"""FetchEngine cache hit, request coalescing, and fetch error helpers.

[POS]
Mixin: freshness-only LRU cache hits (expired entries always synchronously revalidate or refetch),
shielded request-coalescing waits, and unified fetch error normalization.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from langchain_core.documents import Document

from myrm_agent_harness.core.security.guards.ssrf import SSRFSecurityError

from .types import NO_SHARED_RESULT, CachedDocument, InFlightFetch, NoSharedResult

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine

logger = logging.getLogger(__name__)


async def await_shared_result(
    in_flight: InFlightFetch, *, url: str, timeout_s: float
) -> Document | NoSharedResult | None:
    """Wait for the fetch another caller has in flight for the same URL.

    ``NO_SHARED_RESULT`` means it has nothing to share: its leader was cancelled, or it did not finish within
    ``timeout_s``. The wait is shielded, so cancelling this waiter (or its timeout) never cancels the future that
    the leader and the other waiters are bound to.
    """
    try:
        return await asyncio.wait_for(asyncio.shield(in_flight), timeout=timeout_s)
    except TimeoutError:
        logger.warning(f"Request coalescing timeout ({timeout_s}s), retrying: {url}")
        return NO_SHARED_RESULT


class FetchEngineCacheMixin:
    def _handle_cache_hit(
        self: FetchEngine,
        cached_item: CachedDocument,
        is_expired: bool,
        url: str,
        cache_key: str,
    ) -> Document | None:
        """Return cached document only while fresh; expired entries must revalidate or refetch."""
        del cache_key
        cache_age = time.time() - cached_item.cached_at

        if not is_expired:
            logger.info("Cache hit (fresh, age=%.1fs): %s", cache_age, url)
            return cached_item.doc

        if self._enable_http_validation and (cached_item.etag or cached_item.last_modified):
            logger.info("Cache expired (age=%.1fs), sending conditional request: %s", cache_age, url)
        else:
            logger.info("Cache expired (age=%.1fs, no validation headers), refetching: %s", cache_age, url)
        return None

    async def _handle_fetch_error(
        self: FetchEngine,
        url: str,
        cache_key: str,
        future: InFlightFetch,
        error: Exception,
    ) -> Document | None:
        """Unified fetch error handling."""
        self._fail_cache.set(cache_key, True)

        if isinstance(error, SSRFSecurityError):
            logger.warning(f"URL blocked by outbound policy: {url} — {error}")
            if not future.done():
                future.set_result(None)
            return None
        if isinstance(error, asyncio.TimeoutError):
            logger.warning(f"Crawl timeout: {url}")
            if not future.done():
                future.set_result(None)
            return None
        if isinstance(error, (ConnectionError, OSError)):
            logger.warning(f"Network error: {url} — {error}")
            if not future.done():
                future.set_result(None)
            return None

        logger.error(f"Unexpected error during crawl: {url}", exc_info=True)
        if not future.done():
            future.set_exception(error)
        raise
