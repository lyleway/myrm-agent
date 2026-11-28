"""Cost computation engine with provenance tracking and cache-aware pricing.

[INPUT]
- toolkits.llms.catalog::cost_per_token (POS: cache-aware pricing from the model catalog)
- model / prompt_tokens / completion_tokens / cache_read_tokens / cache_write_tokens

[OUTPUT]
- CostStatus: Cost provenance enum (actual / estimated / unknown)
- CostResult: Computed cost in USD with provenance status, and the cache savings of the same call
- compute_cost_by_tokens(): Cache-aware cost and cache savings from token counts

[POS]
Token-economics core of the framework. A model the catalog prices under its own provider is billed as ``ACTUAL``;
a gateway-qualified name (``my-gateway/gpt-4o``) that only resolves once the gateway segment is stripped is billed
as ``ESTIMATED`` so a borrowed price is never presented as the vendor's own. An unpriced model is ``UNKNOWN``, never
zero, so a missing price cannot pass for a free call. The cache savings of a call are the same call billed without
the cache minus its bill, read from the same catalog entry, price tier and token clamps, so the two can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from myrm_agent_harness.toolkits.llms.catalog import cost_per_token

# A picodollar is far below any billable amount and far above the float noise left by subtracting two bills of the
# same money (a model with no cache prices bills cached tokens at the input rate, and the two bills then differ in
# their last bits).
_SAVINGS_DECIMALS: Final = 12


class CostStatus(StrEnum):
    """Cost provenance — how the cost figure was obtained."""

    ACTUAL = "actual"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CostResult:
    """Computed cost in USD with provenance, and what the provider cache saved on the same call.

    ``cache_savings_usd`` is negative when the premium of writing the cache exceeds the discount of reading it.
    """

    usd: float = 0.0
    status: CostStatus = CostStatus.UNKNOWN
    cache_savings_usd: float = 0.0

    @property
    def is_known(self) -> bool:
        return self.status != CostStatus.UNKNOWN


def _priced(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    provider: str | None,
) -> tuple[float, float]:
    """(total USD, cache savings USD) for the model, or zeros when the catalog has no price for it.

    The uncached bill is read only when the call touched the cache; the completion is billed alike in both, so the
    savings are a difference of prompt costs.
    """
    costs = cost_per_token(
        model,
        prompt_tokens,
        completion_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        provider=provider,
    )
    if costs is None:
        return 0.0, 0.0
    total = costs[0] + costs[1]
    if not (cache_read_tokens or cache_write_tokens):
        return total, 0.0
    uncached = cost_per_token(model, prompt_tokens, completion_tokens, provider=provider) or costs
    # ``or 0.0`` turns the negative zero a rounded noise leaves into a plain zero.
    return total, round(uncached[0] - costs[0], _SAVINGS_DECIMALS) or 0.0


def compute_cost_by_tokens(
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    custom_llm_provider: str | None = None,
    request_model: str | None = None,
) -> CostResult:
    """Cache-aware cost and cache savings from token counts; ``prompt_tokens`` includes the cached tokens."""
    target = (request_model or model or "").strip()
    if not target or (prompt_tokens <= 0 and completion_tokens <= 0):
        return CostResult()

    usd, savings = _priced(
        target, prompt_tokens, completion_tokens, cache_read_tokens, cache_write_tokens, custom_llm_provider
    )
    if usd > 0:
        return CostResult(usd=usd, status=CostStatus.ACTUAL, cache_savings_usd=savings)

    _, separator, remainder = target.partition("/")
    if separator:
        usd, savings = _priced(remainder, prompt_tokens, completion_tokens, cache_read_tokens, cache_write_tokens, None)
        if usd > 0:
            return CostResult(usd=usd, status=CostStatus.ESTIMATED, cache_savings_usd=savings)
    return CostResult()


__all__ = [
    "CostResult",
    "CostStatus",
    "compute_cost_by_tokens",
]
