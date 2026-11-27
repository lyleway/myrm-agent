"""The home a CLI keeps its login in, and how a launch is relocated into the home of an account."""

from __future__ import annotations

from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import account_env, known_backends, profile_for
from myrm_agent_harness.toolkits.acp.runtime._base import build_safe_env
from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig

_ROOT = Path("/vol/accounts/work")

# What each CLI honours to keep everything under one directory, and where in it the CLI then looks.
_ISOLATED = {
    "claude": ({"CLAUDE_CONFIG_DIR": str(_ROOT)}, _ROOT),
    "codex": ({"CODEX_HOME": str(_ROOT)}, _ROOT),
    "qwen": ({"QWEN_HOME": str(_ROOT)}, _ROOT),
    # Gemini's variable stands in for $HOME, and it keeps its login in the system keychain unless told to use a file.
    "gemini": ({"GEMINI_CLI_HOME": str(_ROOT), "GEMINI_FORCE_FILE_STORAGE": "true"}, _ROOT / ".gemini"),
}


class TestResolveHome:
    @pytest.mark.parametrize(
        ("backend", "expected"),
        [("claude", ".claude"), ("codex", ".codex"), ("gemini", ".gemini"), ("qwen", ".qwen")],
    )
    def test_without_a_variable_it_is_a_directory_of_the_users_home(self, backend: str, expected: str) -> None:
        profile = profile_for(backend)
        assert profile is not None

        assert profile.resolve_home({"HOME": "/home/u"}) == Path("/home/u") / expected

    @pytest.mark.parametrize("backend", sorted(_ISOLATED))
    def test_the_variable_relocates_it(self, backend: str) -> None:
        profile = profile_for(backend)
        assert profile is not None
        env = {"HOME": "/home/u", profile.home_env: "/vol/elsewhere"}

        resolved = profile.resolve_home(env)

        assert resolved == (Path("/vol/elsewhere") / ".gemini" if backend == "gemini" else Path("/vol/elsewhere"))

    def test_every_known_agent_can_be_relocated(self) -> None:
        for backend in known_backends():
            profile = profile_for(backend)
            assert profile is not None
            assert profile.home_env


class TestIsolatedEnv:
    @pytest.mark.parametrize("backend", sorted(_ISOLATED))
    def test_the_variables_and_the_directory_the_cli_then_uses(self, backend: str) -> None:
        profile = profile_for(backend)
        assert profile is not None
        expected_env, expected_home = _ISOLATED[backend]

        env = profile.isolated_env(_ROOT)

        assert env == expected_env
        assert profile.resolve_home(env) == expected_home
        assert set(profile.relocation_vars) == set(expected_env)

    def test_every_known_agent_is_covered_here(self) -> None:
        assert set(known_backends()) == set(_ISOLATED)


class TestAccountEnv:
    def test_no_account_no_variables(self) -> None:
        assert account_env("claude", None) == {}
        assert account_env("/opt/custom/agent", None) == {}

    @pytest.mark.parametrize("command", ["claude", "/usr/local/bin/claude", "claude.cmd"])
    def test_a_known_agent_is_relocated(self, command: str) -> None:
        assert account_env(command, str(_ROOT)) == {"CLAUDE_CONFIG_DIR": str(_ROOT)}

    def test_a_relative_home_is_refused(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            account_env("claude", "accounts/work")

    @pytest.mark.parametrize("command", ["/opt/custom/agent", "claude-code-acp", "", None])
    def test_a_command_that_is_not_a_known_agent_has_no_variable_to_set(self, command: str | None) -> None:
        with pytest.raises(ValueError, match="known agent"):
            account_env(command, str(_ROOT))


class TestLaunchEnvironment:
    @pytest.mark.parametrize("backend", sorted(_ISOLATED))
    def test_the_agent_is_started_in_the_home_of_its_account(self, backend: str) -> None:
        config = RuntimeConfig(backend_type="cli", command=backend, account_home=str(_ROOT))

        env = build_safe_env(config, {"PATH": "/usr/bin", "HOME": "/home/u"})

        expected_env, _ = _ISOLATED[backend]
        assert env == {"PATH": "/usr/bin", "HOME": "/home/u", **expected_env}

    @pytest.mark.parametrize("backend", sorted(_ISOLATED))
    def test_a_subscription_backend_keeps_it_though_two_of_the_variables_carry_a_provider_prefix(
        self, backend: str
    ) -> None:
        config = RuntimeConfig(
            backend_type="cli",
            command=backend,
            auth_mode="subscription",
            env={"OPENAI_API_KEY": "injected"},
            account_home=str(_ROOT),
        )

        env = build_safe_env(config, {"PATH": "/usr/bin"})

        assert "OPENAI_API_KEY" not in env
        for name, value in _ISOLATED[backend][0].items():
            assert env[name] == value

    def test_the_account_wins_over_the_configured_environment_and_the_hosts_own_home(self) -> None:
        config = RuntimeConfig(
            backend_type="cli",
            command="claude",
            auth_mode="api_key",
            env={"CLAUDE_CONFIG_DIR": "/somewhere/else"},
            account_home=str(_ROOT),
        )

        env = build_safe_env(config, {"CLAUDE_CONFIG_DIR": "/host/claude"})

        assert env["CLAUDE_CONFIG_DIR"] == str(_ROOT)

    def test_without_an_account_the_hosts_own_home_is_inherited_by_every_agent(self) -> None:
        host = {"GEMINI_CLI_HOME": "/vol/gemini", "GEMINI_FORCE_FILE_STORAGE": "true", "QWEN_HOME": "/vol/qwen"}

        assert build_safe_env(RuntimeConfig(backend_type="acp", command="gemini"), host) == {
            "GEMINI_CLI_HOME": "/vol/gemini",
            "GEMINI_FORCE_FILE_STORAGE": "true",
        }
        assert build_safe_env(RuntimeConfig(backend_type="acp", command="qwen"), host) == {"QWEN_HOME": "/vol/qwen"}


class TestRegistration:
    def test_a_pool_takes_an_agent_that_runs_as_an_account(self) -> None:
        pool = RuntimePool()

        pool.register("work", RuntimeConfig(backend_type="cli", command="claude", account_home=str(_ROOT)))

        assert pool.available_backends == ["work"]

    @pytest.mark.parametrize(
        ("command", "home"),
        [("claude", "accounts/work"), ("/opt/custom/agent", str(_ROOT))],
    )
    def test_a_pool_refuses_a_home_the_agent_could_not_be_started_in(self, command: str, home: str) -> None:
        pool = RuntimePool()

        with pytest.raises(ValueError, match="account_home"):
            pool.register("work", RuntimeConfig(backend_type="cli", command=command, account_home=home))

        assert pool.available_backends == []
