"""FetchEngine shared types and constants.

[POS]
Dataclasses and result aliases shared by FetchEngine and its internal mixins.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from langchain_core.documents import Document

SuccessResult = list[tuple[str, Document]]
FailedResult = list[tuple[str, None]]


class NoSharedResult:
    """What an in-flight fetch resolves to when its leader ended without a result the waiters can use (cancelled)."""


NO_SHARED_RESULT = NoSharedResult()

# What callers coalesced on one URL wait for: the leader's page, its miss, or ``NO_SHARED_RESULT``.
InFlightFetch = asyncio.Future[Document | NoSharedResult | None]

# 403 anti-crawl / 429 rate-limit: stop local retries and allow optional L4 remote escalation when configured.
DEGRADABLE_4XX = frozenset({403, 429})


@dataclass(slots=True)
class CachedDocument:
    """Cached Document with HTTP validation metadata."""

    doc: Document
    etag: str | None = None
    last_modified: str | None = None
    cached_at: float = 0.0
