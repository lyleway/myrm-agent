"""Tests for sandbox-aware health report presentation."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from myrm_agent_harness.observability.diagnostics.protocols import HealthReport

from app.core.infra.health.health_presenter import present_health_report
from app.platform_utils.deployment_capabilities import DeploymentCapabilities, get_deployment_capabilities


def _capabilities(*, is_sandbox_instance: bool) -> DeploymentCapabilities:
    """The deployment's capabilities with the one flag these tests are about set: a flag added later never breaks them."""
    return replace(get_deployment_capabilities(), is_sandbox_instance=is_sandbox_instance)


def _sandbox_caps() -> DeploymentCapabilities:
    return _capabilities(is_sandbox_instance=True)


def test_sandbox_system_resources_fix_suggestion_rewritten() -> None:
    report = HealthReport(
        component_name="SystemResources",
        status="warn",
        message="CPU high",
        fix_suggestion="Close unused applications to free memory.",
    )
    with patch(
        "app.core.infra.health.health_presenter.get_deployment_capabilities",
        return_value=_sandbox_caps(),
    ):
        presented = present_health_report(report)
    assert presented.fix_suggestion == "Resource limits are managed by the hosting platform."


def _local_caps() -> DeploymentCapabilities:
    return _capabilities(is_sandbox_instance=False)


def test_local_system_resources_unchanged() -> None:
    report = HealthReport(
        component_name="SystemResources",
        status="warn",
        message="CPU high",
        fix_suggestion="Close unused applications.",
    )
    with patch(
        "app.core.infra.health.health_presenter.get_deployment_capabilities",
        return_value=_local_caps(),
    ):
        presented = present_health_report(report)
    assert presented.fix_suggestion == "Close unused applications."
