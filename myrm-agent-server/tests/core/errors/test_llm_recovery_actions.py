"""Tests for sandbox-aware recovery action URLs."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from myrm_agent_harness.toolkits.llms.errors import FailoverReason

from app.core.errors.llm_errors import generate_recovery_actions
from app.platform_utils.deployment_capabilities import DeploymentCapabilities, get_deployment_capabilities


def _capabilities(*, uses_platform_budget: bool) -> DeploymentCapabilities:
    """The deployment's capabilities with the one flag these tests are about set: a flag added later never breaks them."""
    return replace(get_deployment_capabilities(), uses_platform_budget=uses_platform_budget)


def test_billing_recovery_top_up_points_to_subscription_in_sandbox() -> None:
    with patch(
        "app.core.errors.llm_errors.get_deployment_capabilities",
        return_value=_capabilities(uses_platform_budget=True),
    ):
        actions = generate_recovery_actions(FailoverReason.BILLING, "en")

    top_up = next(action for action in actions if action["id"] == "top_up")
    assert top_up["url"] == "/subscription"


def test_billing_recovery_top_up_points_to_pricing_in_local() -> None:
    with patch(
        "app.core.errors.llm_errors.get_deployment_capabilities",
        return_value=_capabilities(uses_platform_budget=False),
    ):
        actions = generate_recovery_actions(FailoverReason.BILLING, "en")

    top_up = next(action for action in actions if action["id"] == "top_up")
    assert top_up["url"] == "/pricing"


def test_provider_policy_blocked_recovery_actions_en_and_zh() -> None:
    actions_en = generate_recovery_actions(FailoverReason.PROVIDER_POLICY_BLOCKED, "en")
    action_ids_en = [a["id"] for a in actions_en]
    assert action_ids_en == ["switch_to_api_key", "switch_model"]
    assert actions_en[0]["label"] == "Switch to API Key"
    assert actions_en[0]["url"] == "/settings/models"
    assert actions_en[1]["label"] == "Switch Model"
    assert actions_en[1]["url"] == "/settings/models"

    actions_zh = generate_recovery_actions(FailoverReason.PROVIDER_POLICY_BLOCKED, "zh")
    assert actions_zh[0]["label"] == "改用 API Key"
    assert actions_zh[1]["label"] == "切换模型"


def test_long_context_tier_recovery_actions() -> None:
    actions_en = generate_recovery_actions(FailoverReason.LONG_CONTEXT_TIER, "en")
    action_ids_en = [a["id"] for a in actions_en]
    assert action_ids_en == ["switch_to_api_key", "start_new_chat", "switch_model"]
    assert actions_en[0]["label"] == "Switch to API Key"
    assert actions_en[1]["label"] == "New Chat"
    assert actions_en[1]["url"] == "/"
    assert actions_en[2]["label"] == "Switch Model"
