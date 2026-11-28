"""The deployment's list of delegated agents: which a command or name refers to, and whether this deployment runs it.

[POS]
Tests for app/services/external_agents/deployment_policy.py: a sandbox blocks Claude Code and Codex however they are
spelled, never blocks anything else, and a deployment on the user's own machine blocks nothing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.config.deploy_mode import get_deploy_mode
from app.platform_utils.deployment_capabilities import _reset_capabilities_cache_for_testing
from app.services.external_agents.deployment_policy import blocked_backend


def _use_deploy_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("DEPLOY_MODE", mode)
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


@pytest.fixture(autouse=True)
def _restore_deploy_mode() -> Iterator[None]:
    yield
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


class TestASandbox:
    @pytest.mark.parametrize(
        ("command", "backend"),
        [
            ("claude", "claude"),
            ("codex", "codex"),
            ("CLAUDE", "claude"),
            ("/usr/local/bin/codex", "codex"),
            ("C:\\Users\\me\\AppData\\Roaming\\npm\\claude.cmd", "claude"),
            ("claude-code", "claude"),
            ("codex-cli", "codex"),
            ("  claude  ", "claude"),
        ],
    )
    def test_blocks_the_agent_a_command_names(self, monkeypatch: pytest.MonkeyPatch, command: str, backend: str) -> None:
        _use_deploy_mode(monkeypatch, "sandbox")

        assert blocked_backend(command) == backend

    @pytest.mark.parametrize("command", ["gemini", "qwen", "/opt/bin/my-agent", "python", "", "   ", None])
    def test_leaves_every_other_command_alone(self, monkeypatch: pytest.MonkeyPatch, command: str | None) -> None:
        _use_deploy_mode(monkeypatch, "sandbox")

        assert blocked_backend(command) is None

    @pytest.mark.parametrize("override", ["1", "true", "0"])
    def test_cannot_be_unblocked_by_the_environment(self, monkeypatch: pytest.MonkeyPatch, override: str) -> None:
        monkeypatch.setenv("MYRM_ALLOW_LOCAL_SKILLS", override)
        monkeypatch.setenv("WEBUI_MODE", "true")
        monkeypatch.setenv("WEBUI_REMOTE_MODE", "true")
        _use_deploy_mode(monkeypatch, "sandbox")

        assert blocked_backend("claude") == "claude"
        assert blocked_backend("codex") == "codex"


class TestADeploymentOnTheUsersOwnMachine:
    @pytest.mark.parametrize("mode", ["local", "tauri"])
    @pytest.mark.parametrize("command", ["claude", "codex", "/usr/local/bin/claude", "gemini", "qwen"])
    def test_blocks_nothing(self, monkeypatch: pytest.MonkeyPatch, mode: str, command: str) -> None:
        _use_deploy_mode(monkeypatch, mode)

        assert blocked_backend(command) is None

    @pytest.mark.parametrize("webui_remote", ["false", "true"])
    def test_blocks_nothing_when_the_webui_is_exposed_from_the_users_own_machine(
        self, monkeypatch: pytest.MonkeyPatch, webui_remote: str
    ) -> None:
        monkeypatch.setenv("WEBUI_MODE", "true")
        monkeypatch.setenv("WEBUI_REMOTE_MODE", webui_remote)
        _use_deploy_mode(monkeypatch, "local")

        assert blocked_backend("claude") is None
        assert blocked_backend("codex") is None
