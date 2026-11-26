"""Tests for the web fetch escalation context binding."""

from __future__ import annotations

from myrm_agent_harness.toolkits.web_fetch.escalation.context import (
    bind_web_fetch_escalation_context,
    get_bound_escalation_providers,
)
from myrm_agent_harness.toolkits.web_fetch.escalation.protocols import EscalationFetchResult


class _Provider:
    provider_id = "stub"

    async def fetch_url(self, url: str, *, max_chars: int = 0) -> EscalationFetchResult:
        return EscalationFetchResult(url=url, content="x", provider_id="stub")


def test_bind_context_sets_and_resets_providers() -> None:
    assert get_bound_escalation_providers() is None
    with bind_web_fetch_escalation_context(providers=[_Provider()]):
        assert get_bound_escalation_providers() is not None
    assert get_bound_escalation_providers() is None
