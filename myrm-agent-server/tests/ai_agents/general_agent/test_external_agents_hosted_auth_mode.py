"""Which credential a delegated CLI runs on, per deployment.

A deployment that does not allow subscription login runs every delegated CLI on an API key, whatever the stored
configuration says; a local deployment keeps the configured choice.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest

from app.ai_agents.general_agent.external_agents_runtime_config import (
    _auth_mode,
    _config_fingerprint,
    _register_backends_on_pool,
)
from app.config.deploy_mode import get_deploy_mode
from app.platform_utils.deployment_capabilities import _reset_capabilities_cache_for_testing


def _use_deploy_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("DEPLOY_MODE", mode)
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


@pytest.fixture(autouse=True)
def _restore_deploy_mode() -> Iterator[None]:
    yield
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


@pytest.mark.parametrize("configured", [{}, {"authMode": "subscription"}, {"authMode": "api_key"}, {"authMode": "?"}])
def test_a_sandbox_runs_every_delegated_cli_on_an_api_key(monkeypatch: pytest.MonkeyPatch, configured: dict) -> None:
    _use_deploy_mode(monkeypatch, "sandbox")

    assert _auth_mode(configured) == "api_key"


@pytest.mark.parametrize("mode", ["local", "tauri"])
def test_a_local_deployment_keeps_the_configured_choice(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    _use_deploy_mode(monkeypatch, mode)

    assert _auth_mode({}) == "subscription"
    assert _auth_mode({"authMode": "subscription"}) == "subscription"
    assert _auth_mode({"authMode": "api_key"}) == "api_key"


@pytest.mark.parametrize("backend", ["gemini", "qwen"])
def test_the_backend_registered_in_a_sandbox_runs_on_an_api_key(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    _use_deploy_mode(monkeypatch, "sandbox")
    pool = MagicMock()

    _register_backends_on_pool(pool, [{"name": backend, "command": backend, "authMode": "subscription"}])

    assert pool.register.call_args.args[1].auth_mode == "api_key"


def test_the_pool_fingerprint_follows_the_effective_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored subscription choice and an explicit API-key choice are the same runtime in a sandbox."""
    stored = [{"name": "gemini", "command": "gemini", "authMode": "subscription"}]
    explicit = [{"name": "gemini", "command": "gemini", "authMode": "api_key"}]

    _use_deploy_mode(monkeypatch, "sandbox")
    assert _config_fingerprint(stored) == _config_fingerprint(explicit)

    _use_deploy_mode(monkeypatch, "local")
    assert _config_fingerprint(stored) != _config_fingerprint(explicit)
