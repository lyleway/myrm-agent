"""Named accounts of external agents: made, listed with their login state, logged in to, removed.

[POS]
Tests for app/api/external_agents/accounts_router.py and the ``account`` field of login, terminal login and logout: an account
is a home of its own, so what is done to one never reaches the CLI's own home or another account, and a deployment that
keeps no subscription logins refuses to make one while still letting a leftover be seen and removed.
"""

from __future__ import annotations

import shlex
import subprocess
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
_CLI_HOME_VARS = ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "GEMINI_CLI_HOME", "QWEN_HOME")


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


def _use_deploy_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("DEPLOY_MODE", mode)
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


@pytest.fixture(autouse=True)
def _local_deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _use_deploy_mode(monkeypatch, "local")
    yield
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


@pytest.fixture
def default_home(tmp_path: Path) -> Path:
    """The user's own home, where the CLIs keep the login they have no account for."""
    return tmp_path / "home"


@pytest.fixture
def accounts_root(tmp_path: Path) -> Path:
    return (tmp_path / "data" / "external-agents" / "accounts").resolve()


@pytest.fixture
def client(tmp_path: Path, default_home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    default_home.mkdir()
    monkeypatch.setenv("HOME", str(default_home))
    monkeypatch.setenv("MYRM_DATA_DIR", str(tmp_path / "data"))
    for var in _CLI_HOME_VARS:
        monkeypatch.delenv(var, raising=False)
    original = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.router.lifespan_context = original


@pytest.fixture(autouse=True)
def _no_cli_installed() -> Iterator[None]:
    detector = MagicMock()
    detector.detect = AsyncMock(return_value=[])
    with patch("myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector", return_value=detector):
        yield


def _make(client: TestClient, backend: str = "codex", name: str = "work") -> None:
    assert client.post(f"{_API}/accounts", json={"backend": backend, "name": name}).status_code == 201


def _listed(client: TestClient) -> list[dict[str, object]]:
    return client.get(f"{_API}/accounts").json()["accounts"]


class TestAccountLifecycle:
    def test_there_are_none_to_begin_with(self, client) -> None:
        resp = client.get(f"{_API}/accounts")

        assert resp.status_code == 200
        assert resp.json() == {"accounts": []}

    def test_a_new_account_is_an_empty_home_that_is_not_logged_in(self, client, accounts_root) -> None:
        resp = client.post(f"{_API}/accounts", json={"backend": "claude", "name": "work"})

        assert resp.status_code == 201
        assert resp.json() == {"backend": "claude", "name": "work"}
        assert list((accounts_root / "claude" / "work").iterdir()) == []
        assert _listed(client) == [
            {"backend": "claude", "name": "work", "authenticated": False, "authStatus": "not_authenticated"}
        ]

    def test_accounts_are_listed_by_agent_then_name(self, client) -> None:
        for backend, name in (("qwen", "b"), ("claude", "z"), ("claude", "a"), ("codex", "m")):
            _make(client, backend, name)

        assert [(row["backend"], row["name"]) for row in _listed(client)] == [
            ("claude", "a"),
            ("claude", "z"),
            ("codex", "m"),
            ("qwen", "b"),
        ]

    def test_the_same_name_can_belong_to_two_agents(self, client) -> None:
        _make(client, "claude", "work")
        _make(client, "codex", "work")

        assert [row["backend"] for row in _listed(client)] == ["claude", "codex"]

    def test_making_one_that_exists_is_a_conflict(self, client) -> None:
        _make(client)

        resp = client.post(f"{_API}/accounts", json={"backend": "codex", "name": "work"})

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    @pytest.mark.parametrize("name", ["Work", "../x", "a/b", "a.b", "-lead", "x" * 33, " "])
    def test_a_name_that_cannot_be_a_directory_is_refused(self, client, accounts_root, name: str) -> None:
        resp = client.post(f"{_API}/accounts", json={"backend": "codex", "name": name})

        assert resp.status_code == 400
        assert not accounts_root.exists()

    def test_an_unknown_agent_has_no_accounts(self, client) -> None:
        resp = client.post(f"{_API}/accounts", json={"backend": "no-such-agent", "name": "work"})

        assert resp.status_code == 400
        assert "Unknown external agent backend" in resp.json()["detail"]

    def test_removing_one_takes_its_login_and_leaves_the_others(self, client, accounts_root) -> None:
        _make(client, "codex", "work")
        _make(client, "codex", "home")
        (accounts_root / "codex" / "work" / "auth.json").write_text('{"token": "x"}', encoding="utf-8")

        resp = client.delete(f"{_API}/accounts/codex/work")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert not (accounts_root / "codex" / "work").exists()
        assert [row["name"] for row in _listed(client)] == ["home"]

    def test_removing_one_that_does_not_exist_is_not_found(self, client) -> None:
        resp = client.delete(f"{_API}/accounts/codex/ghost")

        assert resp.status_code == 404

    def test_removing_with_an_unusable_name_is_refused_and_touches_nothing(self, client, accounts_root) -> None:
        _make(client)

        assert client.delete(f"{_API}/accounts/codex/Work").status_code == 400
        assert client.delete(f"{_API}/accounts/no-such-agent/work").status_code == 400
        assert (accounts_root / "codex" / "work").is_dir()


class TestLoginStateOfAnAccount:
    def test_a_credential_in_the_account_shows_it_logged_in(self, client, accounts_root) -> None:
        _make(client)
        (accounts_root / "codex" / "work" / "auth.json").write_text('{"token": "x"}', encoding="utf-8")

        assert _listed(client)[0]["authenticated"] is True

    def test_a_login_in_the_default_home_does_not_log_an_account_in(self, client, default_home) -> None:
        _make(client)
        (default_home / ".codex").mkdir()
        (default_home / ".codex" / "auth.json").write_text('{"token": "x"}', encoding="utf-8")

        assert _listed(client)[0]["authenticated"] is False

    def test_the_cli_is_asked_in_the_accounts_own_home(self, client, tmp_path, accounts_root) -> None:
        """Claude Code keeps its login outside any file, so the state comes from the CLI run inside the account."""
        _make(client, "claude", "work")
        cli = tmp_path / "claude"
        cli.write_text(
            '#!/bin/sh\nif [ -f "$CLAUDE_CONFIG_DIR/.signed-in" ]; then echo \'{"loggedIn": true, "authMethod": "claude.ai"}\';'
            ' else echo \'{"loggedIn": false, "authMethod": "none"}\'; fi\n'
        )
        cli.chmod(0o755)
        found = MagicMock()
        found.name = "claude"
        found.path = str(cli)
        detector = MagicMock()
        detector.detect = AsyncMock(return_value=[found])
        with patch("myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector", return_value=detector):
            assert _listed(client)[0]["authenticated"] is False
            (accounts_root / "claude" / "work" / ".signed-in").write_text("", encoding="utf-8")
            assert _listed(client)[0]["authenticated"] is True


def _plant_login(home: Path, content: str) -> None:
    """The credential file Codex keeps its login in, in ``home``: what the CLI leaves when the user has logged in."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(content, encoding="utf-8")


class TestLogoutActsOnTheAccount:
    def test_a_logout_removes_that_accounts_login_only(self, client, accounts_root, default_home) -> None:
        _make(client, "codex", "work")
        _make(client, "codex", "home")
        _plant_login(accounts_root / "codex" / "work", '{"t": 1}')
        _plant_login(accounts_root / "codex" / "home", '{"t": 1}')
        _plant_login(default_home / ".codex", '{"t": 2}')

        resp = client.post(f"{_API}/auth/logout", json={"backend": "codex", "account": "work"})

        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False
        assert not (accounts_root / "codex" / "work" / "auth.json").exists()
        assert (accounts_root / "codex" / "home" / "auth.json").is_file()
        assert (default_home / ".codex" / "auth.json").is_file()

    def test_a_logout_of_an_account_the_machine_does_not_have_is_not_found_and_makes_nothing(self, client, accounts_root) -> None:
        resp = client.post(f"{_API}/auth/logout", json={"backend": "codex", "account": "ghost"})

        assert resp.status_code == 404
        assert not accounts_root.exists()

    def test_an_unusable_account_name_is_refused(self, client) -> None:
        resp = client.post(f"{_API}/auth/logout", json={"backend": "codex", "account": "../x"})

        assert resp.status_code == 400


_SIGNING_IN_CLAUDE = """#!/bin/sh
home="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$home"
case "$1 $2" in
  "auth login") touch "$home/.signed-in" ;;
  "auth status")
    if [ -f "$home/.signed-in" ]; then echo '{"loggedIn": true, "authMethod": "claude.ai"}'
    else echo '{"loggedIn": false, "authMethod": "none"}'; fi ;;
esac
"""


def _detected_claude(tmp_path: Path) -> MagicMock:
    """A ``claude`` that signs in to the home it is started in, and reports whether that home is signed in."""
    cli = tmp_path / "claude"
    cli.write_text(_SIGNING_IN_CLAUDE, encoding="utf-8")
    cli.chmod(0o755)
    found = MagicMock()
    found.name = "claude"
    found.path = str(cli)
    detector = MagicMock()
    detector.detect = AsyncMock(return_value=[found])
    return detector


class TestTerminalLoginActsOnTheAccount:
    @staticmethod
    def _ask(client: TestClient, account: str | None) -> dict[str, object]:
        body: dict[str, object] = {"backend": "claude"}
        if account is not None:
            body["account"] = account
        resp = client.post(f"{_API}/auth/terminal-login", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_the_command_moves_the_cli_into_the_home_of_the_account(self, client, tmp_path, accounts_root) -> None:
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector", return_value=_detected_claude(tmp_path)
        ):
            answer = self._ask(client, "work")

        home = accounts_root / "claude" / "work"
        assert home.is_dir()
        assert answer["account"] == "work"
        assert answer["commands"]["posix"] == (  # type: ignore[index]
            f"CLAUDE_CONFIG_DIR={shlex.quote(str(home))} {shlex.quote(str(tmp_path / 'claude'))} auth login --claudeai"
        )

    def test_what_the_user_pastes_signs_in_that_account_and_no_other(self, client, tmp_path, accounts_root, default_home) -> None:
        _make(client, "claude", "personal")
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector", return_value=_detected_claude(tmp_path)
        ):
            assert self._ask(client, "work")["authenticated"] is False

            pasted = self._ask(client, "work")["commands"]["posix"]  # type: ignore[index]
            done = subprocess.run(["sh", "-c", pasted], capture_output=True, text=True, check=False)

            assert done.returncode == 0, done.stderr
            assert self._ask(client, "work")["authenticated"] is True
            assert self._ask(client, "personal")["authenticated"] is False
            assert self._ask(client, None)["authenticated"] is False
        assert (accounts_root / "claude" / "work" / ".signed-in").is_file()
        assert not (default_home / ".claude" / ".signed-in").exists()

    def test_without_an_account_the_command_is_for_the_cli_own_home(self, client, tmp_path, accounts_root) -> None:
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector", return_value=_detected_claude(tmp_path)
        ):
            answer = self._ask(client, None)

        assert answer["account"] is None
        assert answer["commands"]["posix"] == f"{shlex.quote(str(tmp_path / 'claude'))} auth login --claudeai"  # type: ignore[index]
        assert not accounts_root.exists()

    def test_an_unusable_account_name_makes_nothing(self, client, tmp_path, accounts_root) -> None:
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector", return_value=_detected_claude(tmp_path)
        ):
            resp = client.post(f"{_API}/auth/terminal-login", json={"backend": "claude", "account": "../x"})

        assert resp.status_code == 400
        assert not accounts_root.exists()

    def test_an_agent_that_is_not_installed_makes_no_account(self, client, accounts_root) -> None:
        resp = client.post(f"{_API}/auth/terminal-login", json={"backend": "claude", "account": "work"})

        assert resp.status_code == 409
        assert not accounts_root.exists()


class TestLoginActsOnTheAccount:
    @staticmethod
    def _login(client: TestClient, account: str | None) -> tuple[int, dict[str, object]]:
        seen: dict[str, object] = {}

        class _FakeSession:
            def __init__(self, *args: object, **kwargs: object) -> None:
                seen.update(kwargs)

            async def run(self):
                return
                yield

            async def cancel(self) -> None:
                pass

        body: dict[str, object] = {"command": "codex"}
        if account is not None:
            body["account"] = account
        with patch("myrm_agent_harness.toolkits.acp.auth.CliLoginSession", _FakeSession):
            resp = client.post(f"{_API}/auth/login", json=body)
        return resp.status_code, seen

    def test_the_login_runs_with_the_cli_relocated_into_the_account(self, client, accounts_root) -> None:
        _make(client)

        status, seen = self._login(client, "work")

        assert status == 200
        assert seen["base_env"]["CODEX_HOME"] == str(accounts_root / "codex" / "work")  # type: ignore[index]

    def test_without_an_account_the_login_keeps_the_environment_it_had(self, client) -> None:
        status, seen = self._login(client, None)

        assert status == 200
        assert seen["base_env"] is None

    def test_an_account_the_machine_does_not_have_yet_is_made_before_the_login_starts(self, client, accounts_root) -> None:
        status, seen = self._login(client, "synced")

        assert status == 200
        assert (accounts_root / "codex" / "synced").is_dir()
        assert seen["base_env"]["CODEX_HOME"] == str(accounts_root / "codex" / "synced")  # type: ignore[index]

    def test_an_unusable_account_name_starts_no_login(self, client, accounts_root) -> None:
        status, seen = self._login(client, "../x")

        assert status == 400
        assert seen == {}
        assert not accounts_root.exists()


class TestSandboxKeepsNoSubscriptionLogin:
    @pytest.fixture(autouse=True)
    def _sandbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_deploy_mode(monkeypatch, "sandbox")

    def test_making_an_account_is_refused_and_nothing_is_made(self, client, accounts_root) -> None:
        resp = client.post(f"{_API}/accounts", json={"backend": "codex", "name": "work"})

        assert resp.status_code == 403
        assert "hosted deployments" in resp.json()["detail"]
        assert not accounts_root.exists()

    def test_an_account_left_from_before_can_still_be_seen_and_removed(self, client, accounts_root) -> None:
        (accounts_root / "codex" / "work").mkdir(parents=True)

        assert [row["name"] for row in _listed(client)] == ["work"]
        assert client.delete(f"{_API}/accounts/codex/work").status_code == 200
        assert _listed(client) == []

    def test_a_terminal_login_into_an_account_is_refused(self, client, accounts_root) -> None:
        (accounts_root / "codex" / "work").mkdir(parents=True)

        resp = client.post(f"{_API}/auth/terminal-login", json={"backend": "codex", "account": "work"})

        assert resp.status_code == 403
        assert list((accounts_root / "codex" / "work").iterdir()) == []
