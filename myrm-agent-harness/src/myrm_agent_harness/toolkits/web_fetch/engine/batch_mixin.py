"""FetchEngine batch crawling helpers.

[INPUT]
- web_fetch.engine.types::FailedResult, SuccessResult (POS: Batch result aliases)

[OUTPUT]
- FetchEngineBatchMixin: ``crawl_many`` built on ``FetchEngine.crawl``
- cancel_fetches: cancels fetches that are not already unwinding and waits briefly for them to release their pages

[POS]
Mixin: concurrent crawling of many URLs under one concurrency cap and a per-URL patience.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING

from .types import FailedResult, SuccessResult

if TYPE_CHECKING:
    from collections.abc import Iterable

    from langchain_core.documents import Document

    from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine

logger = logging.getLogger(__name__)

# Once any page is in hand, a URL whose fetch has run this long is no longer waited for.
_STRAGGLER_AFTER_S = 25.0
# With no page in hand, a URL is waited for this long past its crawl timeout (time to close its browser page).
_UNWIND_GRACE_S = 3.0
# When a call is torn down, its cancelled fetches get this long to release their browser pages.
_CANCEL_SETTLE_S = 2.0


def _consume_outcome(task: asyncio.Task[Document | None]) -> None:
    """Mark the outcome of a fetch nobody awaits any more as seen; the engine has already logged its errors."""
    if not task.cancelled():
        task.exception()


async def cancel_fetches(fetches: Iterable[asyncio.Task[Document | None]]) -> None:
    """Cancel the fetches of a call that is torn down (or of an engine shutting down) and let them release their pages.

    A fetch the engine's crawl timeout has already cancelled is unwinding (closing its browser page); a second cancel
    would cut that cleanup short, so it is left to finish on its own.
    """
    cancelled: list[asyncio.Task[Document | None]] = []
    for task in fetches:
        task.add_done_callback(_consume_outcome)
        if not task.cancelling():
            task.cancel()
            cancelled.append(task)
    if cancelled:
        await asyncio.wait(cancelled, timeout=_CANCEL_SETTLE_S)


class FetchEngineBatchMixin:
    async def crawl_many(
        self: FetchEngine,
        urls: list[str],
        *,
        max_concurrency: int = 10,
        force_refresh: bool = False,
        allow_escalation: bool = True,
    ) -> tuple[SuccessResult, FailedResult]:
        """Batch-crawl URLs in parallel under one concurrency cap; one slow URL cannot hold the call hostage.

        Each URL is judged by how long its own fetch has run. With no page in hand yet it is awaited until its crawl
        timeout plus ``_UNWIND_GRACE_S``; once any page is in hand it gets ``_STRAGGLER_AFTER_S``. A URL the call
        stops waiting for is reported as failed, so a call holding a page never ends up empty-handed. Its fetch is
        left running under the engine's own crawl timeout: other callers may be coalesced on it, and its page or
        failure still lands in the engine's caches.
        """
        loop = asyncio.get_running_loop()
        hard_s = self._crawl_timeout + _UNWIND_GRACE_S
        soft_s = min(_STRAGGLER_AFTER_S, hard_s)
        success_results: SuccessResult = []
        failed_results: FailedResult = []
        queued = deque(urls)
        running: dict[asyncio.Task[Document | None], tuple[str, float]] = {}  # task -> (url, start time)

        try:
            while queued or running:
                while queued and len(running) < max_concurrency:
                    url = queued.popleft()
                    fetch = self.crawl(
                        url,
                        force_refresh=force_refresh,
                        allow_escalation=allow_escalation,
                    )
                    running[asyncio.create_task(fetch)] = (url, loop.time())

                patience_s = soft_s if success_results else hard_s
                overdue = [task for task, (_, started_at) in running.items() if loop.time() - started_at >= patience_s]
                if overdue:
                    skipped: list[str] = []
                    for task in overdue:
                        self._detached_fetches.add(task)
                        task.add_done_callback(self._detached_fetches.discard)
                        task.add_done_callback(_consume_outcome)
                        skipped.append(running.pop(task)[0])
                    logger.warning(
                        "Batch crawl stopped waiting for %d slow URL(s) after %.0fs: %s",
                        len(skipped),
                        patience_s,
                        skipped,
                    )
                    failed_results.extend((url, None) for url in skipped)
                    continue

                next_deadline = min(started_at for _, started_at in running.values()) + patience_s
                done, _ = await asyncio.wait(
                    running, timeout=next_deadline - loop.time(), return_when=asyncio.FIRST_COMPLETED
                )
                for task in [t for t in running if t in done]:
                    url, _ = running.pop(task)
                    doc = task.result()
                    if doc is not None:
                        success_results.append((url, doc))
                    else:
                        failed_results.append((url, None))
        except (Exception, asyncio.CancelledError):  # not BaseException: a coroutine closed at teardown must not await
            await cancel_fetches(running)
            raise
        return success_results, failed_results
