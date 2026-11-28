"""Tests for deployment capability registry."""

from __future__ import annotations

from dataclasses import MISSING, fields

import pytest

from app.platform_utils.deployment_capabilities import (
    DeploymentCapabilities,
    _reset_capabilities_cache_for_testing,
    get_deployment_capabilities,
)

# The fields that have no default. A field added later must have one, so that a snapshot built without it stays valid.
_FIELDS_WITHOUT_A_DEFAULT = {
    "allows_local_skills",
    "allows_subscription_login",
    "requires_api_key_auth",
    "uses_platform_budget",
    "validates_mcp_response_size",
    "uses_config_encryption",
    "requires_strict_ws_auth",
    "uses_cp_entitlements",
    "trust_cp_proxy_identity",
    "enables_auth_audit",
    "default_metrics_enabled",
    "runs_sandbox_startup_validation",
    "skips_webui_model_preflight",
    "is_sandbox_instance",
}


@pytest.fixture(autouse=True)
def _clear_caps_cache() -> None:
    from app.config.deploy_mode import get_deploy_mode

    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()
    yield
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


def test_local_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOY_MODE", "local")
    caps = get_deployment_capabilities()
    assert caps.allows_local_skills is True
    assert caps.is_sandbox_instance is False
    assert caps.uses_platform_budget is False
    assert caps.default_metrics_enabled is False
    assert caps.allows_subscription_login is True


def test_sandbox_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOY_MODE", "sandbox")
    caps = get_deployment_capabilities()
    assert caps.allows_local_skills is True
    assert caps.is_sandbox_instance is True
    assert caps.uses_platform_budget is True
    assert caps.validates_mcp_response_size is True
    assert caps.runs_sandbox_startup_validation is True
    assert caps.allows_subscription_login is False


def test_sandbox_capabilities_explicitly_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOY_MODE", "sandbox")
    monkeypatch.setenv("MYRM_ALLOW_LOCAL_SKILLS", "0")
    caps = get_deployment_capabilities()
    assert caps.allows_local_skills is False
    assert caps.is_sandbox_instance is True


def test_tauri_treated_as_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOY_MODE", "tauri")
    caps = get_deployment_capabilities()
    assert caps.allows_local_skills is True
    assert caps.is_sandbox_instance is False
    assert caps.allows_subscription_login is True


@pytest.mark.parametrize("webui_remote", ["false", "true"])
def test_a_webui_on_the_users_own_machine_keeps_subscription_login(monkeypatch: pytest.MonkeyPatch, webui_remote: str) -> None:
    monkeypatch.setenv("DEPLOY_MODE", "local")
    monkeypatch.setenv("WEBUI_MODE", "true")
    monkeypatch.setenv("WEBUI_REMOTE_MODE", webui_remote)

    assert get_deployment_capabilities().allows_subscription_login is True


@pytest.mark.parametrize("override", ["1", "true", "yes", "on"])
def test_a_sandbox_never_allows_subscription_login_whatever_else_is_configured(
    monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    monkeypatch.setenv("DEPLOY_MODE", "sandbox")
    monkeypatch.setenv("MYRM_ALLOW_LOCAL_SKILLS", override)
    monkeypatch.setenv("WEBUI_MODE", "true")
    monkeypatch.setenv("WEBUI_REMOTE_MODE", "true")

    assert get_deployment_capabilities().allows_subscription_login is False


def test_require_local_skills_capability_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.skills._deploy_capability import require_local_skills_capability

    monkeypatch.setenv("DEPLOY_MODE", "local")
    # Should not raise
    require_local_skills_capability()

    monkeypatch.setenv("DEPLOY_MODE", "sandbox")
    # In sandbox PV, local skills are allowed by default
    require_local_skills_capability()


def test_require_local_skills_capability_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from app.api.skills._deploy_capability import require_local_skills_capability

    monkeypatch.setenv("MYRM_ALLOW_LOCAL_SKILLS", "0")
    with pytest.raises(HTTPException) as exc_info:
        require_local_skills_capability()
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Local skills are not available in current deployment mode"


def test_a_field_added_to_the_snapshot_has_a_default() -> None:
    required = {f.name for f in fields(DeploymentCapabilities) if f.default is MISSING and f.default_factory is MISSING}

    assert required == _FIELDS_WITHOUT_A_DEFAULT


def test_a_snapshot_built_without_the_blocked_list_blocks_nothing() -> None:
    snapshot = DeploymentCapabilities(**{name: False for name in _FIELDS_WITHOUT_A_DEFAULT})

    assert snapshot.blocked_external_backends == frozenset()


def test_a_sandbox_blocks_claude_and_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOY_MODE", "sandbox")

    assert get_deployment_capabilities().blocked_external_backends == frozenset({"claude", "codex"})


@pytest.mark.parametrize("mode", ["local", "tauri"])
@pytest.mark.parametrize("webui_remote", ["false", "true"])
def test_a_deployment_on_the_users_own_machine_blocks_no_agent(
    monkeypatch: pytest.MonkeyPatch, mode: str, webui_remote: str
) -> None:
    monkeypatch.setenv("DEPLOY_MODE", mode)
    monkeypatch.setenv("WEBUI_MODE", "true")
    monkeypatch.setenv("WEBUI_REMOTE_MODE", webui_remote)

    assert get_deployment_capabilities().blocked_external_backends == frozenset()


@pytest.mark.parametrize("override", ["1", "true", "0", ""])
def test_a_sandbox_blocks_the_same_agents_whatever_else_is_configured(monkeypatch: pytest.MonkeyPatch, override: str) -> None:
    monkeypatch.setenv("DEPLOY_MODE", "sandbox")
    monkeypatch.setenv("MYRM_ALLOW_LOCAL_SKILLS", override)
    monkeypatch.setenv("WEBUI_MODE", "true")
    monkeypatch.setenv("WEBUI_REMOTE_MODE", "true")

    assert get_deployment_capabilities().blocked_external_backends == frozenset({"claude", "codex"})
