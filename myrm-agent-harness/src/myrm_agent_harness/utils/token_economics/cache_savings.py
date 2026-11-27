"""Net USD savings from prompt-cache reads, after the write premium.

[INPUT]
- toolkits.llms.catalog::model_info (POS: per-token input, cache-read and cache-write prices)
- .usage_fields::extract_cached_tokens / extract_cache_write_tokens (POS: single reader of provider cache accounting fields)

[OUTPUT]
- calculate_cache_savings_usd: estimated net savings of the cache for one call

[POS]
Cache ROI figure for the usage report. A model whose catalog entry lists no cache-read price yields 0: a discount
the catalog does not declare is never assumed.
"""

from __future__ import annotations

from collections.abc import Mapping

from myrm_agent_harness.toolkits.llms.catalog import model_info

from .usage_fields import extract_cache_write_tokens, extract_cached_tokens


def _price(info: Mapping[str, object], key: str) -> float | None:
    value = info.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def calculate_cache_savings_usd(usage: Mapping[str, object], model: str | None) -> float:
    """Net savings in USD: read discount on cached tokens minus the premium paid to write the cache.

    Negative when the write premium exceeds the read savings.
    """
    if not model or not usage:
        return 0.0
    read_tokens = extract_cached_tokens(usage)
    write_tokens = extract_cache_write_tokens(usage)
    if not read_tokens and not write_tokens:
        return 0.0
    info = model_info(model)
    if info is None:
        return 0.0
    input_price = _price(info, "input_cost_per_token") or 0.0
    read_price = _price(info, "cache_read_input_token_cost")
    if read_price is None:
        return 0.0
    write_price = _price(info, "cache_creation_input_token_cost")
    write_premium = max(0.0, write_price - input_price) if write_price is not None else 0.0
    return read_tokens * (input_price - read_price) - write_tokens * write_premium
