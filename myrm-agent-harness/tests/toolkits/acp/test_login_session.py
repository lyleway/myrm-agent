"""The interactive login session: what it makes of a CLI's console output, and when a login counts as done.

A login succeeded when the CLI exited cleanly and says it is logged in. The CLIs' real output is replayed from
recordings (``login_transcripts``): a CLI that colours its output even into a pipe, prints its URL and its code on
separate lines, or writes to stderr must still give the user a clean link and the code. A login the user runs in a
terminal is never started from here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import (
    AuthEventType,
    AuthProfile,
    CliLoginSession,
    LoginStrategy,
    profile_for,
)

_TRANSCRIPTS = Path(__file__).parent / "login_transcripts"

_LOGIN_OK_SCRIPT = (
    "import os, pathlib;"
    "print('To sign in, visit https://auth.example.com/device and enter ABCD-1234');"
    "p = pathlib.Path(os.environ['CODEX_HOME']) / 'auth.json';"
    "p.parent.mkdir(parents=True, exist_ok=True);"
    "p.write_text('{\"ok\": true}')"
)
_LOGIN_FAIL_SCRIPT = "import sys; sys.stderr.write('login failed\\n'); sys.exit(3)"
_PKCE = "A" * 43  # the recordings keep the shape of the one-time values, not the values
_CODEX_LOOPBACK_URL = (
    "https://auth.openai.com/oauth/authorize?response_type=code&client_id=app_EMoamEEZ73f0CkXaXp7hrann"
    f"&redirect_uri=http%3A%2F%2F127.0.0.1%3A1455%2Fauth%2Fcallback&code_challenge={_PKCE}&code_challenge_method=S256"
    f"&state={_PKCE}&scope=openid+profile+email+offline_access+api.connectors.read+api.connectors.invoke"
    "&id_token_add_organizations=true&codex_cli_simplified_flow=true&originator=codex_cli_rs"
)
# Writes a recorded output to the stream it was recorded from, as the CLI does.
_REPLAY_SCRIPT = "import sys; stream = getattr(sys, sys.argv[2]); stream.buffer.write(open(sys.argv[1], 'rb').read()); stream.flush()"


def _login_profile(login_args: tuple[str, ...], remote_login_args: tuple[str, ...] = ()) -> AuthProfile:
    return AuthProfile(
        backend="codex",
        home_env="CODEX_HOME",
        home_dir=".codex",
        credential_files=("auth.json",),
        login_strategy=LoginStrategy.BROWSER_OAUTH,
        login_args=login_args,
        logout_args=None,
        api_key_env=("OPENAI_API_KEY",),
        remote_login_args=remote_login_args,
    )


def _session(tmp_path: Path, login_args: tuple[str, ...], *, timeout_seconds: int = 300) -> CliLoginSession:
    return CliLoginSession(
        sys.executable,
        _login_profile(login_args),
        base_env={**os.environ, "CODEX_HOME": str(tmp_path / ".codex")},
        timeout_seconds=timeout_seconds,
    )


class TestCliLoginSession:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", ["claude", "gemini", "qwen"])
    async def test_a_login_the_user_runs_in_a_terminal_is_not_started(self, backend: str) -> None:
        profile = profile_for(backend)
        assert profile is not None
        events = [e async for e in CliLoginSession(backend, profile).run()]
        assert [e.type for e in events] == [AuthEventType.ERROR]
        assert "terminal" in events[0].message

    @pytest.mark.asyncio
    async def test_the_login_has_no_input_to_wait_on(self, tmp_path: Path) -> None:
        """A CLI that asks for something is told there is nothing, instead of waiting out the timeout."""
        events = [
            e async for e in _session(tmp_path, ("-c", "import sys; print('stdin:' + repr(sys.stdin.read()))")).run()
        ]

        assert any(e.message == "stdin:''" for e in events)

    @pytest.mark.asyncio
    async def test_successful_login_persists_and_succeeds(self, tmp_path: Path) -> None:
        events = [e async for e in _session(tmp_path, ("-c", _LOGIN_OK_SCRIPT)).run()]

        assert AuthEventType.SUCCESS in [e.type for e in events]
        prompts = [e for e in events if e.type is AuthEventType.PROMPT]
        assert any(e.url == "https://auth.example.com/device" for e in prompts)
        assert any(e.code == "ABCD-1234" for e in prompts)
        assert (tmp_path / ".codex" / "auth.json").is_file()

    @pytest.mark.asyncio
    async def test_a_failed_login_ends_with_what_the_cli_said_last(self, tmp_path: Path) -> None:
        events = [e async for e in _session(tmp_path, ("-c", _LOGIN_FAIL_SCRIPT)).run()]

        assert events[-1].type is AuthEventType.ERROR
        assert events[-1].message == "login failed"

    @pytest.mark.asyncio
    async def test_a_failed_login_that_said_nothing_names_its_exit_code(self, tmp_path: Path) -> None:
        events = [e async for e in _session(tmp_path, ("-c", "import sys; sys.exit(4)")).run()]

        assert events[-1].type is AuthEventType.ERROR
        assert events[-1].message == "Login exited with code 4"

    @pytest.mark.asyncio
    async def test_missing_executable_emits_error(self, tmp_path: Path) -> None:
        session = CliLoginSession(
            "/nonexistent/codex-binary-xyz",
            _login_profile(("login",)),
            base_env={**os.environ, "CODEX_HOME": str(tmp_path / ".codex")},
        )
        events = [e async for e in session.run()]
        assert any(e.type is AuthEventType.ERROR for e in events)

    @pytest.mark.asyncio
    async def test_login_timeout_emits_error(self, tmp_path: Path) -> None:
        events = [e async for e in _session(tmp_path, ("-c", "import time; time.sleep(60)"), timeout_seconds=1).run()]

        assert events[-1].type is AuthEventType.ERROR
        assert "timed out" in events[-1].message

    @pytest.mark.asyncio
    async def test_a_clean_exit_without_a_login_is_an_error(self, tmp_path: Path) -> None:
        events = [e async for e in _session(tmp_path, ("-c", "pass")).run()]

        assert events[-1].type is AuthEventType.ERROR
        assert "reports no login" in events[-1].message

    @pytest.mark.asyncio
    async def test_cancel_terminates_process_group(self, tmp_path: Path) -> None:
        session = _session(tmp_path, ("-c", "import time; time.sleep(60)"))
        iterator = session.run()
        await anext(iterator)  # STATUS event: process spawned
        await session.cancel()
        # cancel() is idempotent and safe after termination.
        await session.cancel()

    @pytest.mark.asyncio
    async def test_a_blank_console_line_is_not_an_event(self, tmp_path: Path) -> None:
        events = [e async for e in _session(tmp_path, ("-c", "print('')")).run()]

        assert not any(e.message == "" for e in events)


class TestWhatTheUserIsShown:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("recording", "stream", "url", "code"),
        [
            pytest.param(
                "codex_login.stderr",
                "stderr",
                _CODEX_LOOPBACK_URL,
                None,
                id="codex-loopback",
            ),
            pytest.param(
                "codex_login_device_auth.stdout",
                "stdout",
                "https://auth.openai.com/codex/device",
                "WXYZ-12345",
                id="codex-device-code",
            ),
        ],
    )
    async def test_a_real_clis_output_gives_the_link_and_the_code_the_user_needs(
        self, tmp_path: Path, recording: str, stream: str, url: str, code: str | None
    ) -> None:
        path = _TRANSCRIPTS / f"{recording}.txt"
        events = [e async for e in _session(tmp_path, ("-c", _REPLAY_SCRIPT, str(path), stream)).run()]

        prompts = [e for e in events if e.type is AuthEventType.PROMPT]
        assert [e.url for e in prompts if e.url] == [url]
        assert [e.code for e in prompts if e.code] == ([code] if code else [])
        assert not any("\x1b" in e.message for e in events)

    @pytest.mark.asyncio
    async def test_the_link_and_the_code_of_a_device_flow_arrive_as_separate_events(self, tmp_path: Path) -> None:
        path = _TRANSCRIPTS / "codex_login_device_auth.stdout.txt"
        events = [e async for e in _session(tmp_path, ("-c", _REPLAY_SCRIPT, str(path), "stdout")).run()]

        prompts = [e for e in events if e.type is AuthEventType.PROMPT]
        assert [(e.url, e.code) for e in prompts] == [
            ("https://auth.openai.com/codex/device", None),
            (None, "WXYZ-12345"),
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("address", ["http://localhost:1455.", "http://127.0.0.1:1455/", "http://[::1]:1455"])
    async def test_the_address_of_the_cli_own_callback_server_is_no_link(self, tmp_path: Path, address: str) -> None:
        script = f"print('Starting local login server on {address}')"
        events = [e async for e in _session(tmp_path, ("-c", script)).run()]

        assert not any(e.type is AuthEventType.PROMPT for e in events)
        assert any(address in e.message for e in events if e.type is AuthEventType.STATUS)

    @pytest.mark.asyncio
    async def test_a_url_with_upper_case_runs_in_its_query_gives_no_code(self, tmp_path: Path) -> None:
        script = "print('visit https://auth.example.com/x?session=ABCD-1234&k=2')"
        events = [e async for e in _session(tmp_path, ("-c", script)).run()]

        prompt = next(e for e in events if e.type is AuthEventType.PROMPT)
        assert prompt.url == "https://auth.example.com/x?session=ABCD-1234&k=2"
        assert prompt.code is None


class TestRemoteLogin:
    @pytest.mark.asyncio
    async def test_a_remote_browser_gets_the_login_that_works_from_anywhere(self, tmp_path: Path) -> None:
        profile = _login_profile(("-c", "print('ARGS:local')"), remote_login_args=("-c", "print('ARGS:remote')"))
        base_env = {**os.environ, "CODEX_HOME": str(tmp_path / ".codex")}

        local = [e.message async for e in CliLoginSession(sys.executable, profile, base_env=base_env).run()]
        remote = [
            e.message async for e in CliLoginSession(sys.executable, profile, base_env=base_env, remote=True).run()
        ]

        assert "ARGS:local" in local
        assert "ARGS:remote" in remote

    def test_a_login_that_already_works_from_anywhere_has_no_remote_variant(self) -> None:
        claude = profile_for("claude")
        assert claude is not None

        assert (
            claude.login_command(remote=True) == claude.login_command(remote=False) == ("auth", "login", "--claudeai")
        )

    def test_codex_asks_for_the_device_code_flow_for_a_remote_browser(self) -> None:
        codex = profile_for("codex")
        assert codex is not None

        assert codex.login_command(remote=False) == ("login",)
        assert codex.login_command(remote=True) == ("login", "--device-auth")
