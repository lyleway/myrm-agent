"""Tests for the ACP subscription auth subsystem.

Covers auth profiles, credential detection/import/clear, and auth_mode-aware
environment sanitization. The interactive login session is in ``test_login_session``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import (
    AuthStatus,
    CredentialStorageDisabledError,
    CredentialStore,
    known_backends,
    profile_for,
)
from myrm_agent_harness.toolkits.acp.runtime._base import build_safe_env
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig

_POSIX = os.name == "posix"


class TestProfiles:
    def test_known_backends(self) -> None:
        assert set(known_backends()) == {"codex", "claude", "gemini", "qwen"}

    def test_permission_tables_cover_the_agents_that_need_them(self) -> None:
        """Every agent the host launches by flags has a strip table, and exactly the CLI agents have a mode mapping.

        The app-server agent (codex) has neither: its approval policy and sandbox are set with every turn.
        """
        from myrm_agent_harness.toolkits.acp.auth import _launch_args

        _launch_args._check_tables()
        assert set(_launch_args._PERMISSION_FLAGS_BY_BACKEND) == set(known_backends()) - {"codex"}
        assert set(_launch_args._PERMISSION_MODE_ARGS) == {"claude"}

    def test_permission_table_drift_is_rejected_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A half-configured backend must raise with diagnostics instead of mis-resolving."""
        from myrm_agent_harness.toolkits.acp.auth import _launch_args

        monkeypatch.delitem(_launch_args._PERMISSION_FLAGS_BY_BACKEND, "qwen")
        with pytest.raises(ValueError, match="out of sync with _PROFILES"):
            _launch_args._check_tables()

    def test_resolve_rejects_a_half_configured_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """resolve_cli_args must fail loudly, never merge against missing permissions."""
        from myrm_agent_harness.toolkits.acp.auth import _launch_args

        monkeypatch.setattr(
            _launch_args,
            "_PERMISSION_MODE_ARGS",
            {k: v for k, v in _launch_args._PERMISSION_MODE_ARGS.items() if k != "claude"},
        )
        with pytest.raises(ValueError, match="out of sync with _PROFILES"):
            _launch_args.resolve_cli_args("claude", ["-p"], "safe")

    def test_a_mode_mapping_on_an_acp_agent_is_rejected_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An ACP agent asks the host; a mode mapping for it would never be read, so it is a mistake."""
        from myrm_agent_harness.toolkits.acp.auth import _launch_args

        monkeypatch.setitem(_launch_args._PERMISSION_MODE_ARGS, "qwen", {"safe": ("--approval-mode", "plan")})
        with pytest.raises(ValueError, match="out of sync with _PROFILES"):
            _launch_args.resolve_acp_args("qwen", [])

    def test_cli_launch_args_only_exist_for_an_agent_driven_as_a_cli_process(self) -> None:
        from myrm_agent_harness.toolkits.acp.auth._profiles import cli_launch_args

        assert cli_launch_args("no-such-cli") == []
        assert cli_launch_args("claude") == ["-p", "--output-format", "stream-json", "--verbose"]
        for backend in ("gemini", "qwen", "codex"):
            assert cli_launch_args(backend) == []

    @pytest.mark.parametrize("mode", ["safe", "allow_all"])
    def test_the_cli_backend_resolves_arguments_in_every_mode(self, mode: str) -> None:
        from myrm_agent_harness.toolkits.acp.auth._launch_args import resolve_cli_args

        assert resolve_cli_args("claude", [], mode), f"claude/{mode} resolved to no arguments"

    @pytest.mark.parametrize("backend", ["gemini", "qwen"])
    def test_every_acp_backend_resolves_arguments(self, backend: str) -> None:
        from myrm_agent_harness.toolkits.acp.auth._launch_args import resolve_acp_args

        assert resolve_acp_args(backend, []).args, f"{backend} resolved to no arguments"

    def test_mode_owned_flags_replace_stale_config_flags(self) -> None:
        """Stored configs carrying an old permission flag must not override the mode."""
        from myrm_agent_harness.toolkits.acp.auth._launch_args import resolve_cli_args

        # A stored bypass flag must be replaced by the selected mode's own permission choice.
        stale_bypass = resolve_cli_args("claude", ["--dangerously-skip-permissions"], "safe")
        assert "--dangerously-skip-permissions" not in stale_bypass
        assert stale_bypass[stale_bypass.index("--permission-mode") + 1] == "default"
        # The `--flag=value` spelling escapes a plain name lookup and must be stripped.
        claude_eq = resolve_cli_args("claude", ["--permission-mode=plan"], "allow_all")
        assert claude_eq.count("--permission-mode") == 1
        assert "--permission-mode=plan" not in claude_eq
        # A tool allowlist has no substitute in the mapping, so it must not be dropped.
        kept = resolve_cli_args("claude", ["--allowedTools", "Bash(git:*)"], "safe")
        assert kept[-2:] == ["--allowedTools", "Bash(git:*)"]

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("codex", "codex"),
            ("codex-cli", "codex"),
            ("claude-code", "claude"),
            ("/usr/local/bin/codex", "codex"),
            ("gemini.cmd", "gemini"),
            ("qwen", "qwen"),
        ],
    )
    def test_profile_for_resolves(self, token: str, expected: str) -> None:
        profile = profile_for(token)
        assert profile is not None
        assert profile.backend == expected

    def test_profile_for_unknown(self) -> None:
        assert profile_for("totally-unknown-agent") is None
        assert profile_for("") is None

    def test_scriptable_login(self) -> None:
        assert profile_for("codex").scriptable_login is True  # type: ignore[union-attr]
        assert profile_for("gemini").scriptable_login is False  # type: ignore[union-attr]

    def test_resolve_home_default(self) -> None:
        profile = profile_for("codex")
        assert profile is not None
        home = profile.resolve_home({"HOME": "/home/u"})
        assert str(home) == "/home/u/.codex"

    def test_resolve_home_env_override(self) -> None:
        profile = profile_for("codex")
        assert profile is not None
        home = profile.resolve_home({"HOME": "/home/u", "CODEX_HOME": "/persistent/.codex"})
        assert str(home) == "/persistent/.codex"


class TestCredentialStore:
    def test_state_not_authenticated(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        state = store.state("codex")
        assert state.status is AuthStatus.NOT_AUTHENTICATED
        assert state.authenticated is False

    def test_state_unknown_backend(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        assert store.state("mystery").status is AuthStatus.UNKNOWN

    def test_import_then_authenticated(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        state = store.import_credential("codex", '{"tokens": {"access_token": "abc"}}')
        assert state.status is AuthStatus.AUTHENTICATED
        assert store.is_authenticated("codex") is True
        written = tmp_path / ".codex" / "auth.json"
        assert written.is_file()
        assert json.loads(written.read_text())["tokens"]["access_token"] == "abc"

    @pytest.mark.skipif(not _POSIX, reason="POSIX file permissions")
    def test_import_sets_owner_only_perms(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        store.import_credential("codex", '{"x": 1}')
        mode = (tmp_path / ".codex" / "auth.json").stat().st_mode & 0o777
        assert mode == 0o600

    def test_import_env_override_home(self, tmp_path: Path) -> None:
        persistent = tmp_path / "persistent" / ".codex"
        store = CredentialStore({"HOME": str(tmp_path), "CODEX_HOME": str(persistent)})
        store.import_credential("codex", '{"x": 1}')
        assert (persistent / "auth.json").is_file()

    def test_import_claude_credentials_file(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        store.import_credential("claude", '{"claudeAiOauth": {"accessToken": "t"}}')
        assert (tmp_path / ".claude" / ".credentials.json").is_file()

    def test_import_empty_raises(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        with pytest.raises(ValueError, match="empty"):
            store.import_credential("codex", "   ")

    def test_import_invalid_json_raises(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        with pytest.raises(ValueError, match="not valid JSON"):
            store.import_credential("codex", "not json")

    def test_import_unknown_backend_raises(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        with pytest.raises(ValueError, match="No auth profile"):
            store.import_credential("mystery", '{"x": 1}')

    @pytest.mark.parametrize("bad", ["../escape.json", "sub/dir.json", ".."])
    def test_import_bad_filename_raises(self, tmp_path: Path, bad: str) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        with pytest.raises(ValueError, match="Invalid credential filename"):
            store.import_credential("codex", '{"x": 1}', filename=bad)

    def test_import_empty_filename_uses_default(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        store.import_credential("codex", '{"x": 1}', filename="")
        assert (tmp_path / ".codex" / "auth.json").is_file()

    def test_clear_removes_credentials(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        store.import_credential("codex", '{"x": 1}')
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

    def test_import_oversized_credential_raises(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        oversized = '{"x": "' + "a" * (300 * 1024) + '"}'
        with pytest.raises(ValueError, match="exceeds"):
            store.import_credential("codex", oversized)

    def test_clear_unknown_backend_raises(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        with pytest.raises(ValueError, match="No auth profile"):
            store.clear("mystery")

    def test_clear_survives_unlink_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})
        store.import_credential("codex", '{"x": 1}')

        real_unlink = tmp_path.__class__.unlink

        def _flaky_unlink(self: Path, missing_ok: bool = False) -> None:
            if self.name.endswith("auth.json"):
                raise OSError("permission denied")
            real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(tmp_path.__class__, "unlink", _flaky_unlink)

        state = store.clear("codex")
        assert state.status is AuthStatus.NOT_AUTHENTICATED


class TestCredentialStoreThatKeepsNothing:
    """A deployment that must not hold a user's subscription credentials builds a store that refuses them."""

    @pytest.mark.parametrize("backend", ["codex", "claude", "gemini", "qwen"])
    def test_import_is_refused_before_anything_is_written(self, tmp_path: Path, backend: str) -> None:
        store = CredentialStore({"HOME": str(tmp_path)}, accepts_new_credentials=False)

        with pytest.raises(CredentialStorageDisabledError):
            store.import_credential(backend, '{"x": 1}')

        assert list(tmp_path.iterdir()) == []
        assert store.is_authenticated(backend) is False

    def test_refusal_does_not_depend_on_the_content_being_valid(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)}, accepts_new_credentials=False)

        with pytest.raises(CredentialStorageDisabledError):
            store.import_credential("codex", "")
        with pytest.raises(CredentialStorageDisabledError):
            store.import_credential("mystery", "x")

    def test_a_credential_stored_before_the_policy_applied_can_still_be_seen_and_removed(self, tmp_path: Path) -> None:
        CredentialStore({"HOME": str(tmp_path)}).import_credential("codex", '{"x": 1}')
        store = CredentialStore({"HOME": str(tmp_path)}, accepts_new_credentials=False)

        assert store.is_authenticated("codex") is True
        assert store.clear("codex").status is AuthStatus.NOT_AUTHENTICATED
        assert store.is_authenticated("codex") is False

    def test_the_default_store_keeps_credentials(self, tmp_path: Path) -> None:
        store = CredentialStore({"HOME": str(tmp_path)})

        assert store.import_credential("codex", '{"x": 1}').authenticated is True


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
