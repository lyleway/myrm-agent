"""In-memory call limiters for the host fetch and model-call proxies.

[INPUT]
- (none)

[OUTPUT]
- CallLimiter: per-key sliding-window rate plus an in-flight cap.
- FETCH_LIMITER / AI_LIMITER: the process-wide limiters used by the fetch proxy and the model-call proxy.

[POS]
Business layer. Keeps one runaway page from hammering a third-party host, the model provider or the server's connection pool. State is
process memory on purpose: it only has to bound a burst, not account for usage.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Final

PER_MINUTE: Final = 60
MAX_IN_FLIGHT: Final = 2
_WINDOW_S: Final = 60.0


class CallLimiter:
    def __init__(self, per_minute: int, max_in_flight: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._per_minute = per_minute
        self._max_in_flight = max_in_flight
        self._clock = clock
        self._starts: dict[str, deque[float]] = defaultdict(deque)
        self._in_flight: dict[str, int] = defaultdict(int)

    def try_acquire(self, key: str) -> bool:
        """Claim one call slot; the caller must ``release`` it when the call finishes."""
        now = self._clock()
        starts = self._starts[key]
        while starts and now - starts[0] >= _WINDOW_S:
            starts.popleft()
        if len(starts) >= self._per_minute or self._in_flight[key] >= self._max_in_flight:
            return False
        starts.append(now)
        self._in_flight[key] += 1
        return True

    def release(self, key: str) -> None:
        self._in_flight[key] = max(0, self._in_flight[key] - 1)


FETCH_LIMITER: Final = CallLimiter(PER_MINUTE, MAX_IN_FLIGHT)
# A model call takes seconds and costs money, so its pace is well below a page's fetches.
AI_LIMITER: Final = CallLimiter(20, MAX_IN_FLIGHT)
