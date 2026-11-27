"""Logging out goes through the CLI, because only the CLI can remove a login it keeps outside its home.

[POS]
Tests for the logout endpoint, the ``remote`` field of the login endpoint and the removal of an account in
app/api/external_agents/: a stand-in ``claude`` keeps its login in a ``.signed-in`` marker inside the home it is
relocated into, and removes it only when it is asked to log out, so a login the files alone cannot remove stays visible.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config.deploy_mode import get_deploy_mode
from app.core.security.auth.identity import LOCAL_USER_ID
from app.platform_utils.deployment_capabilities import _reset_capabilities_cache_for_testing
from tests.support.minimal_app import build_minimal_app

app = build_minimal_app(preset="external_agents")

_API = "/api/v1/external-agents"
posix_only = pytest.mark.skipif(os.name != "posix", reason="the stand-in CLI is a shell script")
_CLI_HOME_VARS = ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "GEMINI_CLI_HOME", "QWEN_HOME")
# The login is the marker in the CLI's home. ``auth logout`` removes it unless a ``stuck`` file sits next to the home,
# and leaves a ``logout-called`` note there either way; the provider key the CLI sees is noted in ``logout-key``.
_FAKE_CLAUDE = """#!/bin/sh
home="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
case "$1 $2" in
  "auth status")
    if [ -f "$home/.signed-in" ]; then echo '{"loggedIn": true, "authMethod": "claude.ai"}'; exit 0; fi
    echo '{"loggedIn": false, "authMethod": "none"}'; exit 1 ;;
  "auth logout")
    echo called > "$home/../logout-called"
    printf %s "$ANTHROPIC_API_KEY" > "$home/../logout-key"
    if [ -f "$home/../stuck" ]; then echo "keychain locked" >&2; exit 1; fi
    rm -f "$home/.signed-in"; echo "Successfully logged out"; exit 0 ;;
esac
exit 2
"""


@dataclass(frozen=True, slots=True)
class _FakeIdentity:
    user_id: str = LOCAL_USER_ID
    auth_source: str = "loopback"
    loopback: bool = True
    client_ip: str = "127.0.0.1"


@pytest.fixture(autouse=True)
def _bypass_auth() -> Iterator[None]:
    with patch("app.middleware.auth.resolve_identity", return_value=_FakeIdentity()):
        yield


@asynccontextmanager
async def _noop_lifespan(_app):
    yield


@pytest.fixture(autouse=True)
def _local_deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DEPLOY_MODE", "local")
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()
    yield
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


@pytest.fixture
def default_home(tmp_path: Path) -> Path:
    """The CLI's own home: ``$HOME/.claude``, the home of the login the user made on this machine."""
    return tmp_path / "home" / ".claude"


@pytest.fixture
def accounts_root(tmp_path: Path) -> Path:
    return (tmp_path / "data" / "external-agents" / "accounts").resolve()


@pytest.fixture
def client(tmp_path: Path, default_home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    default_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(default_home.parent))
    monkeypatch.setenv("MYRM_DATA_DIR", str(tmp_path / "data"))
    for var in _CLI_HOME_VARS:
        monkeypatch.delenv(var, raising=False)
    original = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.router.lifespan_context = original


@pytest.fixture
def claude_installed(tmp_path: Path) -> Iterator[Path]:
    """The stand-in ``claude`` is the CLI detected on this machine."""
    cli = tmp_path / "bin" / "claude"
    cli.parent.mkdir()
    cli.write_text(_FAKE_CLAUDE)
    cli.chmod(0o755)
    found = MagicMock()
    found.name = "claude"
    found.path = str(cli)
    detector = MagicMock()
    detector.detect = AsyncMock(return_value=[found])
    with patch("myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector", return_value=detector):
        yield cli


def _sign_in(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".signed-in").write_text("")


def _make_account(client: TestClient, name: str = "work") -> None:
    assert client.post(f"{_API}/accounts", json={"backend": "claude", "name": name}).status_code == 201


@posix_only
class TestLogoutAsksTheCli:
    def test_the_cli_logs_itself_out_of_a_login_no_file_holds(self, client, default_home, claude_installed) -> None:
        _sign_in(default_home)

        resp = client.post(f"{_API}/auth/logout", json={"backend": "claude"})

        assert resp.status_code == 200
        assert resp.json() == {"backend": "claude", "authenticated": False, "authStatus": "not_authenticated"}
        assert not (default_home / ".signed-in").exists()

    def test_a_login_the_cli_could_not_remove_is_still_reported(self, client, default_home, claude_installed) -> None:
        _sign_in(default_home)
        (default_home.parent / "stuck").write_text("")

        resp = client.post(f"{_API}/auth/logout", json={"backend": "claude"})

        assert resp.status_code == 200
        assert resp.json()["authenticated"] is True
        assert (default_home / ".signed-in").exists()

    def test_the_cli_is_not_given_the_operators_provider_key(self, client, default_home, claude_installed, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-operator")
        _sign_in(default_home)

        client.post(f"{_API}/auth/logout", json={"backend": "claude"})

        assert (default_home.parent / "logout-key").read_text() == ""

    def test_an_account_is_logged_out_without_touching_the_default_home(
        self, client, default_home, accounts_root, claude_installed
    ) -> None:
        _make_account(client)
        _sign_in(accounts_root / "claude" / "work")
        _sign_in(default_home)

        resp = client.post(f"{_API}/auth/logout", json={"backend": "claude", "account": "work"})

        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False
        assert not (accounts_root / "claude" / "work" / ".signed-in").exists()
        assert (default_home / ".signed-in").exists()

    def test_without_an_installed_cli_the_files_are_still_removed(self, client, default_home) -> None:
        detector = MagicMock()
        detector.detect = AsyncMock(return_value=[])
        _sign_in(default_home)
        (default_home / ".credentials.json").write_text("{}")

        with patch("myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector", return_value=detector):
            resp = client.post(f"{_API}/auth/logout", json={"backend": "claude"})

        assert resp.status_code == 200
        assert not (default_home / ".credentials.json").exists()
        assert (default_home / ".signed-in").exists()  # only the CLI could have removed this one


@posix_only
class TestRemovingAnAccountLogsItOut:
    def test_the_cli_logs_out_of_the_account_before_its_home_goes(self, client, accounts_root, claude_installed) -> None:
        _make_account(client)
        _sign_in(accounts_root / "claude" / "work")

        resp = client.delete(f"{_API}/accounts/claude/work")

        assert resp.status_code == 200
        assert (accounts_root / "claude" / "logout-called").is_file()
        assert not (accounts_root / "claude" / "work").exists()

    def test_an_account_whose_login_stands_is_kept(self, client, accounts_root, claude_installed) -> None:
        _make_account(client)
        _sign_in(accounts_root / "claude" / "work")
        (accounts_root / "claude" / "stuck").write_text("")

        resp = client.delete(f"{_API}/accounts/claude/work")

        assert resp.status_code == 409
        assert "kept" in resp.json()["detail"]
        assert (accounts_root / "claude" / "work" / ".signed-in").exists()

    def test_an_account_that_was_never_logged_in_is_removed(self, client, accounts_root, claude_installed) -> None:
        _make_account(client)

        assert client.delete(f"{_API}/accounts/claude/work").status_code == 200
        assert not (accounts_root / "claude" / "work").exists()


class TestLoginForARemoteBrowser:
    @staticmethod
    def _login(client: TestClient, **body: object) -> dict[str, object]:
        seen: dict[str, object] = {}

        class _FakeSession:
            def __init__(self, *args: object, **kwargs: object) -> None:
                seen.update(kwargs)

            async def run(self):
                return
                yield

            async def cancel(self) -> None:
                pass

        with patch("myrm_agent_harness.toolkits.acp.auth.CliLoginSession", _FakeSession):
            assert client.post(f"{_API}/auth/login", json={"command": "codex", "sessionId": "s1", **body}).status_code == 200
        return seen

    def test_a_browser_on_another_machine_is_passed_on(self, client) -> None:
        assert self._login(client, remote=True)["remote"] is True

    def test_the_browser_is_taken_to_be_on_this_machine_by_default(self, client) -> None:
        assert self._login(client)["remote"] is False
