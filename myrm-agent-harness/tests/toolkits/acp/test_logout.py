"""Logging out the way the CLI does it: the login of a CLI that keeps it outside its home goes only through the CLI.

A stand-in ``claude`` keeps its login in a ``keychain`` file next to its configuration directory, as the real one does in
the macOS Keychain. Removing credential files leaves that login standing, so ``log_out`` asks the CLI first and
removes the files after, and tells what the CLI reports once both are done.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import AuthStatus, CredentialState, CredentialStore, log_out, profile_for
from tests.toolkits.acp._fake_claude_login import FakeClaude, install_fake_claude

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the stand-in CLI is a shell script")


def _logged_in(fake: FakeClaude, *, credential_file: bool) -> None:
    """A login in the stand-in's keychain, and one in the file the CLI also reads when it is imported."""
    fake.keychain.write_text("logged-in")
    if credential_file:
        (fake.config_dir / ".credentials.json").write_text("{}")


async def _log_out(fake: FakeClaude, **env: str) -> CredentialState:
    return await log_out(CredentialStore(fake.env(**env)), "claude", executable=fake.executable, env=fake.env(**env))


async def test_the_cli_logs_itself_out_and_the_credential_files_go_too(tmp_path: Path) -> None:
    fake = install_fake_claude(tmp_path)
    _logged_in(fake, credential_file=True)

    state = await _log_out(fake)

    assert state.status is AuthStatus.NOT_AUTHENTICATED
    assert not fake.keychain.exists()
    assert not (fake.config_dir / ".credentials.json").exists()


async def test_a_login_the_cli_could_not_remove_is_still_reported(tmp_path: Path) -> None:
    fake = install_fake_claude(tmp_path)
    _logged_in(fake, credential_file=True)
    fake.logout_fails.write_text("")

    state = await _log_out(fake)

    assert state.authenticated  # the CLI says it is still logged in, and that is the answer
    assert fake.keychain.exists()
    assert not (fake.config_dir / ".credentials.json").exists()  # the files go whatever the CLI did


async def test_the_cli_is_not_given_the_operators_provider_keys(tmp_path: Path) -> None:
    fake = install_fake_claude(tmp_path)
    _logged_in(fake, credential_file=False)

    await _log_out(fake, ANTHROPIC_API_KEY="sk-operator")

    assert fake.logout_env.read_text() == ""


async def test_without_the_cli_only_the_files_go(tmp_path: Path) -> None:
    fake = install_fake_claude(tmp_path)
    _logged_in(fake, credential_file=True)

    state = await log_out(CredentialStore(fake.env()), "claude", executable=None, env=fake.env())

    assert state.status is AuthStatus.NOT_AUTHENTICATED
    assert fake.keychain.exists()  # nothing was asked of the CLI, so what it keeps is not touched
    assert not (fake.config_dir / ".credentials.json").exists()


@pytest.mark.parametrize("backend", ["gemini", "qwen"])
async def test_a_cli_without_a_logout_command_is_never_run(tmp_path: Path, backend: str) -> None:
    profile = profile_for(backend)
    assert profile is not None and profile.logout_args is None
    env = {**os.environ, profile.home_env: str(tmp_path)}
    credential = profile.resolve_credential_paths(env)[0]
    credential.parent.mkdir(parents=True, exist_ok=True)
    credential.write_text("{}")

    state = await log_out(CredentialStore(env), backend, executable="/nonexistent/never-run", env=env)

    assert state.status is AuthStatus.NOT_AUTHENTICATED
    assert not credential.exists()


async def test_an_unknown_backend_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No auth profile"):
        await log_out(CredentialStore({}), "nope", executable=None, env={})


@pytest.mark.parametrize(("backend", "args"), [("claude", ("auth", "logout")), ("codex", ("logout",))])
def test_the_agents_that_keep_a_login_the_cli_can_remove_say_how(backend: str, args: tuple[str, ...]) -> None:
    profile = profile_for(backend)
    assert profile is not None

    assert profile.logout_args == args
