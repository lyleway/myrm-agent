"""The login state each real CLI reports about itself is still what ``resolve_login_state`` reads. Offline: no model.

A CLI that stops printing ``loggedIn`` or changes what ``login status`` says would make the parser return nothing and
the credential file take over without anything failing, so each case asserts the parsed report itself; the cases
where the file and the CLI disagree show whose word wins.

Run with ``-m integration``. A CLI that is not installed skips its tests, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import (
    AuthStatus,
    CredentialState,
    CredentialStore,
    LoginReport,
    probe_login,
    resolve_login_state,
)
from myrm_agent_harness.toolkits.acp.auth.launch_env import cli_base_env
from tests.toolkits.acp.cli_contract import (
    CLAUDE_OAUTH,
    CODEX_API_KEY,
    cli_binary,
    probe_env,
    write_credential_file,
)

pytestmark = pytest.mark.integration


async def _report(backend: str, home: Path) -> LoginReport | None:
    binary = cli_binary(backend)
    return await probe_login(backend, executable=binary, env=cli_base_env(binary, probe_env(home)), timeout_seconds=60)


async def _state(backend: str, home: Path) -> CredentialState:
    binary = cli_binary(backend)
    env = probe_env(home)
    return await resolve_login_state(
        CredentialStore(env), backend, executable=binary, env=cli_base_env(binary, env), timeout_seconds=60
    )


async def test_claude_in_an_empty_home_reports_logged_out(tmp_path: Path) -> None:
    assert await _report("claude", tmp_path) == LoginReport(False)


async def test_claude_reads_oauth_credentials_as_a_subscription_login(tmp_path: Path) -> None:
    write_credential_file(tmp_path, "claude", CLAUDE_OAUTH)

    assert await _report("claude", tmp_path) == LoginReport(True, "claude.ai")


async def test_claude_does_not_take_a_credential_file_it_rejects_for_a_login(tmp_path: Path) -> None:
    write_credential_file(tmp_path, "claude", {"unrelated": True})
    assert (
        CredentialStore(probe_env(tmp_path)).state("claude").status is AuthStatus.AUTHENTICATED
    )  # the file check is fooled

    state = await _state("claude", tmp_path)

    assert state.status is AuthStatus.NOT_AUTHENTICATED


async def test_codex_in_an_empty_home_reports_logged_out(tmp_path: Path) -> None:
    assert await _report("codex", tmp_path) == LoginReport(False)


async def test_codex_reads_an_api_key_file_as_a_login(tmp_path: Path) -> None:
    write_credential_file(tmp_path, "codex", CODEX_API_KEY)

    assert await _report("codex", tmp_path) == LoginReport(True, "api_key")
