"""Tests for the ACP subscription auth subsystem.

Covers credential detection/clear and auth_mode-aware environment sanitization. The registry of known agents is in
``drivers/``; the interactive login session is in ``test_login_session``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import AuthStatus, CredentialStore
from myrm_agent_harness.toolkits.acp.runtime._base import build_safe_env
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig
from tests.toolkits.acp._logins import plant_login


class TestCredentialStore:
    def test_state_not_authenticated(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        state = store.state("codex")
        assert state.status is AuthStatus.NOT_AUTHENTICATED
        assert state.authenticated is False

    def test_state_unknown_backend(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        assert store.state("mystery").status is AuthStatus.UNKNOWN

    def test_a_credential_file_in_the_home_means_authenticated(self, tmp_path: Path) -> None:
        env = {"HOME": str(tmp_path)}
        plant_login(env, "codex", '{"tokens": {"access_token": "abc"}}')
        store = CredentialStore(env)

        state = store.state("codex")

        assert state.status is AuthStatus.AUTHENTICATED
        assert state.credential_path == str(tmp_path / ".codex" / "auth.json")
        assert store.is_authenticated("codex") is True

    def test_the_relocated_home_is_the_one_looked_in(self, tmp_path: Path) -> None:
        persistent = tmp_path / "persistent" / ".codex"
        env = {"HOME": str(tmp_path), "CODEX_HOME": str(persistent)}
        plant_login(env, "codex")

        assert (persistent / "auth.json").is_file()
        assert CredentialStore(env).is_authenticated("codex") is True
        assert CredentialStore({"HOME": str(tmp_path)}).is_authenticated("codex") is False

    def test_claude_keeps_its_credentials_in_a_dotfile(self, tmp_path: Path) -> None:
        env = {"HOME": str(tmp_path)}
        plant_login(env, "claude", '{"claudeAiOauth": {"accessToken": "t"}}')

        assert (tmp_path / ".claude" / ".credentials.json").is_file()
        assert CredentialStore(env).is_authenticated("claude") is True

    def test_the_store_offers_no_way_to_write_a_credential(self) -> None:
        """The host holds no login: the CLI makes it, and nothing here accepts one to keep."""
        assert {name for name in dir(CredentialStore) if not name.startswith("_")} == {
            "state",
            "is_authenticated",
            "clear",
        }

    def test_clear_removes_credentials(self, tmp_path: Path) -> None:
        env = {"HOME": str(tmp_path)}
        plant_login(env, "codex")
        store = CredentialStore(env)
        assert store.is_authenticated("codex") is True
        state = store.clear("codex")
        assert state.status is AuthStatus.NOT_AUTHENTICATED
        assert store.is_authenticated("codex") is False

    def test_clear_idempotent(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        assert store.clear("codex").status is AuthStatus.NOT_AUTHENTICATED

    def test_empty_credential_file_not_authenticated(self, tmp_path: Path) -> None:
        cred = tmp_path / ".codex" / "auth.json"
        cred.parent.mkdir(parents=True)
        cred.write_text("")
        store = CredentialStore({"HOME": str(tmp_path)})
        assert store.is_authenticated("codex") is False

    def test_clear_unknown_backend_raises(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        with pytest.raises(ValueError, match="No auth profile"):
            store.clear("mystery")

    def test_clear_survives_unlink_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {"HOME": str(tmp_path)}
        plant_login(env, "codex")
        store = CredentialStore(env)

        real_unlink = tmp_path.__class__.unlink

        def _flaky_unlink(self: Path, missing_ok: bool = False) -> None:
            if self.name.endswith("auth.json"):
                raise OSError("permission denied")
            real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(tmp_path.__class__, "unlink", _flaky_unlink)

        state = store.clear("codex")
        assert state.status is AuthStatus.NOT_AUTHENTICATED


class TestBuildSafeEnvAuthMode:
    def test_subscription_strips_all_provider_secrets(self) -> None:
        cfg = RuntimeConfig(backend_type="cli", command="codex", auth_mode="subscription")
        env = build_safe_env(
            cfg,
            base_env={
                "OPENAI_API_KEY": "o",
                "GEMINI_API_KEY": "g",
                "XAI_API_KEY": "x",
                "QWEN_API_KEY": "q",
                "MISTRAL_API_KEY": "m",
                "PATH": "/bin",
                "HOME": "/h",
            },
        )
        for secret in ("OPENAI_API_KEY", "GEMINI_API_KEY", "XAI_API_KEY", "QWEN_API_KEY", "MISTRAL_API_KEY"):
            assert secret not in env
        assert env["PATH"] == "/bin"
        assert env["HOME"] == "/h"

    def test_subscription_drops_injected_secret_keeps_plain(self) -> None:
        cfg = RuntimeConfig(
            backend_type="cli",
            command="codex",
            auth_mode="subscription",
            env={"OPENAI_API_KEY": "injected", "NODE_OPTIONS": "--max-old-space-size=4096"},
        )
        env = build_safe_env(cfg, base_env={"PATH": "/bin"})
        assert "OPENAI_API_KEY" not in env
        assert env["NODE_OPTIONS"] == "--max-old-space-size=4096"

    def test_api_key_injects_provider_secret(self) -> None:
        cfg = RuntimeConfig(
            backend_type="cli",
            command="codex",
            auth_mode="api_key",
            env={"OPENAI_API_KEY": "injected"},
        )
        env = build_safe_env(cfg, base_env={"OPENAI_API_KEY": "from-host", "PATH": "/bin"})
        assert env["OPENAI_API_KEY"] == "injected"
