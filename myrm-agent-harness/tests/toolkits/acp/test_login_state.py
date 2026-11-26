"""resolve_login_state: the CLI's own word about its login beats a credential file, which is only the fallback."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import AuthStatus, CredentialStore, resolve_login_state

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the CLIs are stood in for by POSIX shell scripts")


def _cli(directory: Path, name: str, body: str) -> str:
    path = directory / name
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)


def _claude_status(logged_in: bool, method: str = "claude.ai") -> str:
    """What ``claude auth status`` does: JSON on stdout, exit 0 when logged in and 1 when not."""
    payload = json.dumps({"loggedIn": logged_in, "authMethod": method if logged_in else "none"})
    return f"echo '{payload}'\nexit {0 if logged_in else 1}\n"


@pytest.fixture
def home(tmp_path: Path) -> dict[str, str]:
    """An environment whose HOME holds no credential file of any CLI."""
    (tmp_path / "home").mkdir()
    return {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}


def _put_claude_credential_file(env: dict[str, str]) -> None:
    credential = Path(env["HOME"]) / ".claude" / ".credentials.json"
    credential.parent.mkdir(parents=True)
    credential.write_text('{"claudeAiOauth": {}}')


class TestClaude:
    async def test_a_login_kept_outside_any_file_is_seen(self, tmp_path: Path, home: dict[str, str]) -> None:
        cli = _cli(tmp_path, "claude", _claude_status(True))

        state = await resolve_login_state(CredentialStore(home), "claude", executable=cli, env=home)

        assert state.status is AuthStatus.AUTHENTICATED
        assert state.detail == "claude.ai"
        assert (
            CredentialStore(home).state("claude").status is AuthStatus.NOT_AUTHENTICATED
        )  # the file check alone is blind

    async def test_a_stale_credential_file_does_not_make_a_logged_out_cli_logged_in(
        self, tmp_path: Path, home: dict[str, str]
    ) -> None:
        _put_claude_credential_file(home)
        cli = _cli(tmp_path, "claude", _claude_status(False))

        state = await resolve_login_state(CredentialStore(home), "claude", executable=cli, env=home)

        assert state.status is AuthStatus.NOT_AUTHENTICATED

    @pytest.mark.parametrize("body", ["echo 'not json'\n", 'echo \'{"loggedIn": "yes"}\'\n', "exit 3\n"])
    async def test_an_answer_that_cannot_be_read_falls_back_to_the_file(
        self, tmp_path: Path, home: dict[str, str], body: str
    ) -> None:
        cli = _cli(tmp_path, "claude", body)
        assert (await resolve_login_state(CredentialStore(home), "claude", executable=cli, env=home)).status is (
            AuthStatus.NOT_AUTHENTICATED
        )

        _put_claude_credential_file(home)
        assert (await resolve_login_state(CredentialStore(home), "claude", executable=cli, env=home)).status is (
            AuthStatus.AUTHENTICATED
        )

    async def test_a_cli_that_hangs_falls_back_to_the_file(self, tmp_path: Path, home: dict[str, str]) -> None:
        _put_claude_credential_file(home)
        cli = _cli(tmp_path, "claude", "sleep 300\n")

        state = await resolve_login_state(
            CredentialStore(home), "claude", executable=cli, env=home, timeout_seconds=0.5
        )

        assert state.status is AuthStatus.AUTHENTICATED


class TestCodex:
    async def test_exit_zero_is_logged_in_and_names_the_method(self, tmp_path: Path, home: dict[str, str]) -> None:
        other = tmp_path / "other"
        other.mkdir()
        subscription = _cli(tmp_path, "codex", 'echo "Logged in using ChatGPT" >&2\n')
        api_key = _cli(other, "codex", 'echo "API key configured - sk-test-***" >&2\n')

        first = await resolve_login_state(CredentialStore(home), "codex", executable=subscription, env=home)
        second = await resolve_login_state(CredentialStore(home), "codex", executable=api_key, env=home)

        assert (first.status, first.detail) == (AuthStatus.AUTHENTICATED, "subscription")
        assert (second.status, second.detail) == (AuthStatus.AUTHENTICATED, "api_key")

    async def test_exit_one_is_logged_out(self, tmp_path: Path, home: dict[str, str]) -> None:
        cli = _cli(tmp_path, "codex", 'echo "Not logged in" >&2\nexit 1\n')

        state = await resolve_login_state(CredentialStore(home), "codex", executable=cli, env=home)

        assert state.status is AuthStatus.NOT_AUTHENTICATED

    async def test_any_other_exit_is_no_answer_and_falls_back_to_the_file(
        self, tmp_path: Path, home: dict[str, str]
    ) -> None:
        cli = _cli(tmp_path, "codex", "exit 2\n")
        codex_home = Path(home["HOME"]) / ".codex"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text("{}")

        state = await resolve_login_state(CredentialStore(home), "codex", executable=cli, env=home)

        assert state.status is AuthStatus.AUTHENTICATED


class TestWithoutAStatusCommand:
    async def test_gemini_is_judged_by_its_credential_file(self, tmp_path: Path, home: dict[str, str]) -> None:
        cli = _cli(tmp_path, "gemini", "exit 99\n")
        assert (await resolve_login_state(CredentialStore(home), "gemini", executable=cli, env=home)).status is (
            AuthStatus.NOT_AUTHENTICATED
        )

        creds = Path(home["HOME"]) / ".gemini"
        creds.mkdir()
        (creds / "oauth_creds.json").write_text("{}")
        assert (await resolve_login_state(CredentialStore(home), "gemini", executable=cli, env=home)).status is (
            AuthStatus.AUTHENTICATED
        )

    async def test_an_unknown_backend_is_unknown(self, tmp_path: Path, home: dict[str, str]) -> None:
        state = await resolve_login_state(CredentialStore(home), "mystery", executable=None, env=home)
        assert state.status is AuthStatus.UNKNOWN

    async def test_a_known_backend_that_is_not_installed_uses_the_file(self, home: dict[str, str]) -> None:
        state = await resolve_login_state(CredentialStore(home), "claude", executable=None, env=home)
        assert state.status is AuthStatus.NOT_AUTHENTICATED
