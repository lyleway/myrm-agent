"""Logging in and out the way the real CLIs do it. Offline: no account, no model, no network.

The harness depends on a few facts about the CLIs' own commands, and each is held here against the installed CLI:

- every command a profile runs to log in, log in from afar, log out or ask about the login, or hands the user to run in
  a terminal, is one the CLI still has;
- a CLI's own logout removes the login a home holds, and ``log_out`` leaves no login anywhere the CLI looks.

The two flows of Codex are not run: ``codex login`` opens a browser and ``codex login --device-auth`` asks OpenAI for a
code, so what they print is replayed from recordings in ``test_login_session`` and their flags are judged here.

Run with ``-m integration``. A CLI that is not installed skips its tests, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names it.
"""

from __future__ import annotations

from itertools import takewhile
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import AuthStatus, CredentialStore, LoginReport, log_out, probe_login
from myrm_agent_harness.toolkits.acp.auth.launch_env import cli_base_env
from myrm_agent_harness.toolkits.acp.drivers import all_drivers, driver_for
from tests.toolkits.acp.cli_contract import (
    CLAUDE_OAUTH,
    CODEX_API_KEY,
    check_args,
    cli_binary,
    options_by_scope,
    probe_env,
    run_probe,
    write_credential_file,
)

pytestmark = pytest.mark.integration

_BACKENDS = ("claude", "codex")
_LOGIN_CONTENT = {"claude": CLAUDE_OAUTH, "codex": CODEX_API_KEY}


def _login_commands() -> list[object]:
    """Every command a profile of ``_BACKENDS`` runs about the login, by purpose."""
    commands: list[object] = []
    for backend in _BACKENDS:
        profile = driver_for(backend)
        assert profile is not None
        for purpose, args in (
            ("login", profile.login_args),
            ("remote-login", profile.remote_login_args),
            ("logout", profile.logout_args or ()),
            ("status", profile.login_probe),
        ):
            if args:
                commands.append(pytest.param(backend, args, id=f"{backend}-{purpose}"))
    return commands


async def _report(backend: str, env: dict[str, str]) -> LoginReport | None:
    binary = cli_binary(backend)
    return await probe_login(backend, executable=binary, env=cli_base_env(binary, env), timeout_seconds=60)


@pytest.fixture(scope="module")
def probe_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("cli-login-probe-home")


def test_every_profile_with_a_login_command_is_judged_by_this_contract() -> None:
    with_commands = {p.backend for p in all_drivers() if p.login_args or p.logout_args or p.login_probe}

    assert with_commands == set(_BACKENDS)


@pytest.mark.parametrize(("backend", "args"), _login_commands())
def test_the_login_commands_of_a_profile_are_accepted(backend: str, args: tuple[str, ...], probe_home: Path) -> None:
    binary = cli_binary(backend)
    words = tuple(takewhile(lambda arg: not arg.startswith("-"), args))
    scopes = options_by_scope(binary, [words[:length] for length in range(len(words) + 1)], probe_home)

    assert check_args(scopes, args, subcommands=frozenset(words)) == []


def test_a_login_command_the_cli_does_not_have_is_refused_by_the_live_judge(probe_home: Path) -> None:
    binary = cli_binary("claude")
    scopes = options_by_scope(binary, [(), ("auth",), ("auth", "login")], probe_home)

    problems = check_args(scopes, ("auth", "login", "--not-a-flag-of-claude"), subcommands=frozenset({"auth", "login"}))

    assert problems == ["--not-a-flag-of-claude is not an option of auth login"]


@pytest.mark.parametrize("backend", _BACKENDS)
async def test_the_cli_logs_itself_out_of_the_login_a_home_holds(backend: str, tmp_path: Path) -> None:
    binary = cli_binary(backend)
    profile = driver_for(backend)
    assert profile is not None and profile.logout_args
    env = probe_env(tmp_path)
    write_credential_file(tmp_path, backend, _LOGIN_CONTENT[backend])
    assert await _report(backend, env) == LoginReport(True, "claude.ai" if backend == "claude" else "api_key")

    code, output = run_probe([binary, *profile.logout_args], cli_base_env(binary, env), tmp_path)

    assert code == 0, output
    assert await _report(backend, env) == LoginReport(False)


@pytest.mark.parametrize("backend", _BACKENDS)
async def test_log_out_leaves_no_login_the_cli_knows_of(backend: str, tmp_path: Path) -> None:
    binary = cli_binary(backend)
    env = probe_env(tmp_path)
    write_credential_file(tmp_path, backend, _LOGIN_CONTENT[backend])
    store = CredentialStore(env)
    assert store.state(backend).status is AuthStatus.AUTHENTICATED

    state = await log_out(store, backend, executable=binary, env=env)

    assert state.status is AuthStatus.NOT_AUTHENTICATED
    assert store.state(backend).status is AuthStatus.NOT_AUTHENTICATED
