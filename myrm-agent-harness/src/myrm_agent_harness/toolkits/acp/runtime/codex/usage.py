"""Token usage of a Codex thread, as the step each model request cost.

[INPUT]
- toolkits.acp.runtime.codex.wire::Json, as_int (POS: Typed reads of the decoded JSON that Codex's app server sends)

[OUTPUT]
- TokenCounts: the counters of one usage report
- UsageMeter: turns the cumulative counters of a thread into the usage of each step

[POS]
The server reports a thread's running totals (and the last request's own counters), while the hosts of a delegated turn
add up what each ``usage_update`` carries. The meter hands out only what is new, so a repeated report adds nothing and
a thread resumed after a restart, whose totals hold its whole history, does not bill that history to the turn.
"""

from __future__ import annotations

from dataclasses import dataclass

from myrm_agent_harness.toolkits.acp.runtime.codex.wire import Json, as_int


@dataclass(frozen=True, slots=True)
class TokenCounts:
    """The counters of one usage report; ``cached`` is the share of ``input`` that came from the prompt cache."""

    input: int = 0
    output: int = 0
    cached: int = 0
    cache_write: int = 0

    @classmethod
    def of(cls, breakdown: Json) -> TokenCounts:
        """The counters of a ``TokenUsageBreakdown`` as the server wrote it."""
        return cls(
            input=as_int(breakdown.get("inputTokens")),
            output=as_int(breakdown.get("outputTokens")),
            cached=as_int(breakdown.get("cachedInputTokens")),
            cache_write=as_int(breakdown.get("cacheWriteInputTokens")),
        )

    def minus(self, other: TokenCounts) -> TokenCounts:
        """What ``self`` has beyond ``other``, never negative (a total that restarted counts from zero)."""
        return TokenCounts(
            input=max(self.input - other.input, 0),
            output=max(self.output - other.output, 0),
            cached=max(self.cached - other.cached, 0),
            cache_write=max(self.cache_write - other.cache_write, 0),
        )

    @property
    def empty(self) -> bool:
        return not (self.input or self.output or self.cached or self.cache_write)


class UsageMeter:
    """The usage of one thread, step by step. One meter lives as long as the process that serves the thread."""

    def __init__(self) -> None:
        self._seen: TokenCounts | None = None

    def step(self, total: TokenCounts, last: TokenCounts) -> TokenCounts:
        """What a report adds: ``total`` beyond what was seen, and on the first report ``last`` alone."""
        baseline = total.minus(last) if self._seen is None else self._seen
        self._seen = total
        return total.minus(baseline)
