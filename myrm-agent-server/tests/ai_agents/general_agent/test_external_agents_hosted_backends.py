"""Which delegated agents a deployment registers: a managed sandbox does not run Claude Code or Codex.

[POS]
Tests for the blocked-agent rule in app/ai_agents/general_agent/external_agents_runtime_config.py: a blocked agent is
neither registered nor part of the pool fingerprint, whatever its auth mode, path or name; a local deployment registers
every agent.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.ai_agents.general_agent.external_agents_runtime_config import (
    _config_fingerprint,
    _normalize_runtime_cfg,
    _register_backends_on_pool,
)
from app.config.deploy_mode import get_deploy_mode
from app.platform_utils.deployment_capabilities import _reset_capabilities_cache_for_testing


def _use_deploy_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("DEPLOY_MODE", mode)
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


@pytest.fixture(autouse=True)
def _restore_deploy_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # A sandbox gives every agent it runs a home under the state root, which must not be the developer's.
    monkeypatch.setenv("MYRM_DATA_DIR", str(tmp_path / "data"))
    yield
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


def _registered(configs: list[dict[str, object]]) -> list[str]:
    """The names the pool was asked to register, in order."""
    pool = MagicMock()
    _register_backends_on_pool(pool, configs)
    return [call.args[0] for call in pool.register.call_args_list]


_BLOCKED_COMMANDS = ["claude", "codex", "/usr/local/bin/claude", "C:\\tools\\codex.cmd", "claude-code", "CODEX"]


class TestASandboxDoesNotRunBlockedAgents:
    @pytest.mark.parametrize("command", _BLOCKED_COMMANDS)
    @pytest.mark.parametrize("auth_mode", ["subscription", "api_key"])
    def test_a_blocked_agent_is_not_registered_on_either_credential(
        self, monkeypatch: pytest.MonkeyPatch, command: str, auth_mode: str
    ) -> None:
        _use_deploy_mode(monkeypatch, "sandbox")

        assert _registered([{"name": "agent", "command": command, "authMode": auth_mode}]) == []

    def test_the_agents_around_a_blocked_one_are_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_deploy_mode(monkeypatch, "sandbox")

        registered = _registered(
            [
                {"name": "first", "command": "gemini"},
                {"name": "blocked", "command": "claude"},
                {"name": "mine", "command": "/opt/bin/my-agent"},
                {"name": "last", "command": "qwen"},
            ]
        )

        assert registered == ["first", "mine", "last"]

    def test_a_blocked_agent_is_reported_once_with_the_reason(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _use_deploy_mode(monkeypatch, "sandbox")

        with caplog.at_level(logging.WARNING):
            _registered([{"name": "work", "command": "codex", "env": {"OPENAI_API_KEY": "sk-secret"}}])

        assert "Skipping external agent 'work': codex is not available in this deployment" in caplog.text
        assert "sk-secret" not in caplog.text

    def test_a_blocked_agent_is_no_agent_to_normalize(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_deploy_mode(monkeypatch, "sandbox")

        assert _normalize_runtime_cfg({"name": "agent", "command": "claude"}) is None
        assert _normalize_runtime_cfg({"name": "agent", "command": "gemini"}) is not None

    def test_a_blocked_agent_is_not_part_of_the_pool_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Adding or removing one does not rebuild a pool that would register the same agents."""
        _use_deploy_mode(monkeypatch, "sandbox")
        kept = [{"name": "gemini", "command": "gemini"}]

        assert _config_fingerprint(kept + [{"name": "claude", "command": "claude"}]) == _config_fingerprint(kept)
        assert _config_fingerprint([{"name": "claude", "command": "claude"}]) == _config_fingerprint([])

    def test_a_command_that_only_resembles_no_agent_is_not_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_deploy_mode(monkeypatch, "sandbox")

        assert _registered([{"name": "mine", "command": "my-agent"}, {"name": "gem", "command": "gemini"}]) == [
            "mine",
            "gem",
        ]


class TestALocalDeploymentRunsEveryAgent:
    @pytest.mark.parametrize("mode", ["local", "tauri"])
    def test_blocked_commands_are_registered(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
        _use_deploy_mode(monkeypatch, mode)
        configs = [{"name": f"agent-{i}", "command": command} for i, command in enumerate(_BLOCKED_COMMANDS)]

        assert _registered(configs) == [f"agent-{i}" for i in range(len(_BLOCKED_COMMANDS))]

    def test_the_same_agent_is_part_of_the_pool_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_deploy_mode(monkeypatch, "local")

        assert _config_fingerprint([{"name": "claude", "command": "claude"}]) != _config_fingerprint([])
