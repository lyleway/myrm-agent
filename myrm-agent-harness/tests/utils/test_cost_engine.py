"""Tests for myrm_agent_harness.utils.token_economics.cost_engine (catalog-priced)."""

import math
from collections.abc import Iterator

import pytest

from myrm_agent_harness.toolkits.llms.catalog import register_model, store
from myrm_agent_harness.utils.token_economics.cost_engine import (
    CostResult,
    CostStatus,
    compute_cost_by_tokens,
)


@pytest.fixture(autouse=True)
def _isolated_registrations(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(store, "_REGISTERED", {})
    yield


class TestCostResult:
    def test_defaults(self) -> None:
        r = CostResult()
        assert r.usd == 0.0
        assert r.status == CostStatus.UNKNOWN
        assert r.cache_savings_usd == 0.0

    @pytest.mark.parametrize("status", [CostStatus.ACTUAL, CostStatus.ESTIMATED])
    def test_priced_status_is_known(self, status: CostStatus) -> None:
        assert CostResult(usd=0.01, status=status).is_known is True

    def test_unknown_is_not_known(self) -> None:
        assert CostResult().is_known is False


class TestUnpriced:
    def test_no_model(self) -> None:
        assert compute_cost_by_tokens(None, 100, 50) == CostResult()
        assert compute_cost_by_tokens("", 100, 50) == CostResult()

    def test_no_tokens(self) -> None:
        assert compute_cost_by_tokens("gpt-4o", 0, 0) == CostResult()
        assert compute_cost_by_tokens("gpt-4o", -1, -1) == CostResult()

    def test_model_missing_from_catalog_is_unknown_not_free(self) -> None:
        assert compute_cost_by_tokens("no-such-model-xyz", 100, 50) == CostResult()

    def test_zero_priced_entry_is_unknown(self) -> None:
        register_model("free-model", {"input_cost_per_token": 0.0, "output_cost_per_token": 0.0})
        assert compute_cost_by_tokens("free-model", 100, 50) == CostResult()


class TestPriced:
    def test_mainstream_model_bills_actual(self) -> None:
        # gpt-4o: 1k prompt @ 2.50/M + 200 completion @ 10.00/M
        result = compute_cost_by_tokens("gpt-4o", 1000, 200)
        assert result.status == CostStatus.ACTUAL
        assert result.usd == pytest.approx(0.0045, abs=1e-6)

    def test_cache_read_is_discounted(self) -> None:
        uncached = compute_cost_by_tokens("gpt-4o", 1000, 200)
        cached = compute_cost_by_tokens("gpt-4o", 1000, 200, cache_read_tokens=800)
        assert cached.usd < uncached.usd
        assert cached.usd == pytest.approx(0.0035, abs=1e-6)

    def test_only_prompt_tokens(self) -> None:
        result = compute_cost_by_tokens("gpt-4o", 100, 0)
        assert result.status == CostStatus.ACTUAL
        assert result.usd == pytest.approx(0.00025, abs=1e-8)

    def test_only_completion_tokens(self) -> None:
        result = compute_cost_by_tokens("gpt-4o", 0, 100)
        assert result.status == CostStatus.ACTUAL
        assert result.usd == pytest.approx(0.001, abs=1e-8)

    def test_provider_qualified_name(self) -> None:
        result = compute_cost_by_tokens("deepseek/deepseek-chat", 5000, 1000)
        assert result.status == CostStatus.ACTUAL
        assert result.usd > 0.0

    def test_request_model_wins_over_response_model(self) -> None:
        by_request = compute_cost_by_tokens("unknown-alias", 1000, 200, request_model="gpt-4o")
        assert by_request.usd == pytest.approx(0.0045, abs=1e-6)

    def test_registered_price_beats_snapshot_and_bills_cache(self) -> None:
        register_model(
            "custom_openai/mimo-test",
            {
                "input_cost_per_token": 0.000002,
                "output_cost_per_token": 0.000006,
                "cache_read_input_token_cost": 0.0000005,
            },
        )
        result = compute_cost_by_tokens("custom_openai/mimo-test", 1000, 500, cache_read_tokens=800)
        # 800 cached @ 0.5/M + 200 fresh @ 2/M + 500 completion @ 6/M
        assert result.usd == pytest.approx(0.0038, abs=1e-8)
        assert result.status == CostStatus.ACTUAL


class TestGatewayFallback:
    def test_gateway_prefix_is_estimated_from_the_stripped_name(self) -> None:
        result = compute_cost_by_tokens("my-gateway/gpt-4o", 1000, 200)
        assert result.status == CostStatus.ESTIMATED
        assert result.usd == pytest.approx(0.0045, abs=1e-6)

    def test_unknown_after_stripping_stays_unknown(self) -> None:
        assert compute_cost_by_tokens("my-gateway/no-such-model-xyz", 1000, 200) == CostResult()

    def test_other_providers_price_is_never_used_as_actual(self) -> None:
        # "deepseek/gpt-4o" must not bill gpt-4o's OpenAI price as the vendor's own price.
        result = compute_cost_by_tokens("deepseek/gpt-4o", 1000, 200)
        assert result.status != CostStatus.ACTUAL


_CACHING = "caching-model"
_TIERED_CACHING = "tiered-caching-model"
_NO_CACHE_PRICE = "no-cache-price-model"
_CACHING_RATES = {
    "input_cost_per_token": 3e-06,
    "output_cost_per_token": 1.5e-05,
    "cache_read_input_token_cost": 3e-07,
    "cache_creation_input_token_cost": 3.75e-06,
}
_TIERED_RATES = {
    **_CACHING_RATES,
    "input_cost_per_token_above_200k_tokens": 6e-06,
    "output_cost_per_token_above_200k_tokens": 2.25e-05,
    "cache_read_input_token_cost_above_200k_tokens": 6e-07,
    "cache_creation_input_token_cost_above_200k_tokens": 7.5e-06,
}


class TestCacheSavings:
    """The savings of a call are the same call billed without the cache minus its bill, from the bill's own prices."""

    @pytest.fixture(autouse=True)
    def _models(self, _isolated_registrations: None) -> None:
        register_model(_CACHING, _CACHING_RATES)
        register_model(_TIERED_CACHING, _TIERED_RATES)
        register_model(_NO_CACHE_PRICE, {"input_cost_per_token": 3e-06, "output_cost_per_token": 1.2e-05})

    def test_a_call_that_touches_no_cache_saves_nothing(self) -> None:
        assert compute_cost_by_tokens(_CACHING, 50_000, 1_000).cache_savings_usd == 0.0

    @pytest.mark.parametrize(
        ("model", "prompt", "cached", "written", "savings"),
        [
            # 50k prompt tokens bill 0.15; with 40k of them read from the cache the same call bills 0.042.
            pytest.param(_CACHING, 50_000, 40_000, 0, 0.108, id="reads"),
            pytest.param(_CACHING, 1_500, 1_000, 500, 0.002325, id="reads-and-writes"),
            pytest.param(_CACHING, 1_000, 0, 1_000, -0.00075, id="writes-alone-cost-their-premium"),
            # A count above the prompt is billed as the whole prompt, so it is saved as the whole prompt.
            pytest.param(_CACHING, 50_000, 90_000, 0, 0.135, id="reads-beyond-the-prompt"),
            # Above 200k prompt tokens every rate of the tier changes: the flat rates would say 0.54.
            pytest.param(_TIERED_CACHING, 300_000, 200_000, 0, 1.08, id="above-the-200k-tier"),
        ],
    )
    def test_the_savings_are_what_the_cache_took_off_the_bill(
        self, model: str, prompt: int, cached: int, written: int, savings: float
    ) -> None:
        result = compute_cost_by_tokens(model, prompt, 1_000, cache_read_tokens=cached, cache_write_tokens=written)
        without_cache = compute_cost_by_tokens(model, prompt, 1_000)

        assert result.cache_savings_usd == pytest.approx(savings, abs=1e-9)
        assert result.cache_savings_usd == pytest.approx(without_cache.usd - result.usd, abs=1e-9)

    def test_a_provider_hint_prices_the_uncached_bill_with_the_same_entry(self) -> None:
        register_model("vendor-a/hinted-model", _CACHING_RATES)

        result = compute_cost_by_tokens(
            "hinted-model", 50_000, 1_000, cache_read_tokens=40_000, custom_llm_provider="vendor-a"
        )

        assert result.status == CostStatus.ACTUAL
        assert result.cache_savings_usd == pytest.approx(0.108, abs=1e-9)

    def test_a_gateway_qualified_name_carries_its_savings_with_the_estimated_status(self) -> None:
        result = compute_cost_by_tokens(f"my-gateway/{_CACHING}", 50_000, 1_000, cache_read_tokens=40_000)

        assert result.status == CostStatus.ESTIMATED
        assert result.cache_savings_usd == pytest.approx(0.108, abs=1e-9)

    @pytest.mark.parametrize(("prompt", "cached", "written"), [(1_500, 495, 165), (50_000, 5_000, 1_666)])
    def test_a_model_with_no_cache_prices_saves_exactly_nothing(self, prompt: int, cached: int, written: int) -> None:
        # Its cached and written tokens bill at the input rate: the two bills are the same money, yet as floats they
        # differ in the last bits (+8.7e-19 and -2.8e-17 USD for these counts). That noise is not a saving.
        result = compute_cost_by_tokens(
            _NO_CACHE_PRICE, prompt, 100, cache_read_tokens=cached, cache_write_tokens=written
        )

        assert result.cache_savings_usd == 0.0
        assert math.copysign(1.0, result.cache_savings_usd) == 1.0

    def test_an_unpriced_model_reports_no_savings(self) -> None:
        assert compute_cost_by_tokens("no-such-model-xyz", 1_000, 50, cache_read_tokens=800) == CostResult()
