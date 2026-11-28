"""Tests for external agent subscription-auth API endpoints.

[POS]
Tests for app/api/external_agents/: login-status reporting, SSE login event
relay, the login a user runs in a terminal, the routes the host does not have
(it takes no credential), and logout.
"""

from __future__ import annotations

import shlex
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.security.auth.identity import LOCAL_USER_ID
from tests.support.minimal_app import build_minimal_app

app = build_minimal_app(preset="external_agents")


@dataclass(frozen=True, slots=True)
class _FakeIdentity:
    user_id: str = LOCAL_USER_ID
    auth_source: str = "loopback"
    loopback: bool = True
    client_ip: str = "127.0.0.1"


@pytest.fixture(autouse=True)
def _bypass_auth():
    """Make TestClient requests pass auth (TestClient is not seen as loopback)."""
    with patch("app.middleware.auth.resolve_identity", return_value=_FakeIdentity()):
        yield


@asynccontextmanager
async def _noop_lifespan(_app):
    yield


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with HOME redirected to a temp dir so credential I/O is hermetic."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)
    original = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.router.lifespan_context = original


class TestAuthStatus:
    """GET /external-agents/auth/status."""

    def test_status_lists_known_backends(self, client):
        detector = MagicMock()
        detector.detect = AsyncMock(return_value=[])
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=detector,
        ):
            resp = client.get("/api/v1/external-agents/auth/status")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["backends"]) == 4
        backends = {b["backend"]: b for b in body["backends"]}
        assert {"codex", "claude", "gemini", "qwen"} <= set(backends)
        codex = backends["codex"]
        assert codex["installed"] is False
        assert codex["authenticated"] is False
        assert "readyForDelegation" not in codex
        assert codex["loginStrategy"] == "browser_oauth"
        assert {name: row["loginStrategy"] for name, row in backends.items()} == {
            "codex": "browser_oauth",
            "claude": "terminal",
            "gemini": "terminal",
            "qwen": "terminal",
        }

    def test_status_names_the_transport_each_agent_runs_over(self, client):
        detector = MagicMock()
        detector.detect = AsyncMock(return_value=[])
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=detector,
        ):
            body = client.get("/api/v1/external-agents/auth/status").json()

        assert {b["backend"]: b["transport"] for b in body["backends"]} == {
            "claude": "cli",
            "codex": "app_server",
            "gemini": "acp",
            "qwen": "acp",
        }

    def test_status_reflects_installed_and_authenticated(self, client, tmp_path):
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "auth.json").write_text('{"token": "x"}', encoding="utf-8")
        found = MagicMock()
        found.name = "codex"
        found.path = "/usr/bin/codex"
        found.version = "1.2.3"
        detector = MagicMock()
        detector.detect = AsyncMock(return_value=[found])
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=detector,
        ):
            resp = client.get("/api/v1/external-agents/auth/status")
        codex = {b["backend"]: b for b in resp.json()["backends"]}["codex"]
        assert codex["installed"] is True
        assert codex["path"] == "/usr/bin/codex"
        assert codex["authenticated"] is True

    def test_status_cli_installed_without_a_login_is_not_authenticated(self, client, tmp_path):
        cli = tmp_path / "claude"
        cli.write_text('#!/bin/sh\necho \'{"loggedIn": false, "authMethod": "none"}\'\n')
        cli.chmod(0o755)
        found = MagicMock()
        found.name = "claude"
        found.path = str(cli)
        found.version = "2.0.0"
        detector = MagicMock()
        detector.detect = AsyncMock(return_value=[found])
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=detector,
        ):
            resp = client.get("/api/v1/external-agents/auth/status")
        claude = {b["backend"]: b for b in resp.json()["backends"]}["claude"]
        assert claude["installed"] is True
        assert claude["authenticated"] is False

    def test_status_asks_the_cli_who_is_logged_in(self, client, tmp_path):
        """A login held outside any credential file (the macOS Keychain) is still seen."""
        cli = tmp_path / "claude"
        cli.write_text('#!/bin/sh\necho \'{"loggedIn": true, "authMethod": "claude.ai"}\'\n')
        cli.chmod(0o755)
        found = MagicMock()
        found.name = "claude"
        found.path = str(cli)
        found.version = "2.0.0"
        detector = MagicMock()
        detector.detect = AsyncMock(return_value=[found])
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=detector,
        ):
            resp = client.get("/api/v1/external-agents/auth/status")
        claude = {b["backend"]: b for b in resp.json()["backends"]}["claude"]
        assert claude["authenticated"] is True
        assert claude["authStatus"] == "authenticated"
        assert not (tmp_path / ".claude" / ".credentials.json").exists()

    def test_status_not_installed_is_not_authenticated(self, client):
        detector = MagicMock()
        detector.detect = AsyncMock(return_value=[])
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=detector,
        ):
            resp = client.get("/api/v1/external-agents/auth/status")
        claude = {b["backend"]: b for b in resp.json()["backends"]}["claude"]
        assert claude["installed"] is False
        assert claude["authenticated"] is False

    def test_status_forces_fresh_detection(self, client):
        detector = MagicMock()
        detector.detect = AsyncMock(return_value=[])
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=detector,
        ):
            resp = client.get("/api/v1/external-agents/auth/status")

        assert resp.status_code == 200
        detector.detect.assert_awaited_once_with(include_version=True, refresh=True)


class TestAuthLogoutErrors:
    """POST /external-agents/auth/logout error paths."""

    def test_logout_unknown_backend_returns_400(self, client):
        resp = client.post(
            "/api/v1/external-agents/auth/logout",
            json={"backend": "no-such-backend"},
        )
        assert resp.status_code == 400


class TestAuthLogin:
    """POST /external-agents/auth/login (SSE)."""

    def test_login_streams_prompt_and_success(self, client):
        from myrm_agent_harness.toolkits.acp.auth import AuthEvent, AuthEventType

        class _FakeSession:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def run(self):
                yield AuthEvent(
                    AuthEventType.PROMPT,
                    message="Visit https://example/device",
                    url="https://example/device",
                    code="ABCD-1234",
                )
                yield AuthEvent(AuthEventType.SUCCESS, message="login complete")

            async def cancel(self) -> None:
                pass

        with patch("myrm_agent_harness.toolkits.acp.auth.CliLoginSession", _FakeSession):
            resp = client.post(
                "/api/v1/external-agents/auth/login",
                json={"command": "codex"},
            )
        assert resp.status_code == 200
        body = resp.text
        assert '"type": "prompt"' in body
        assert "ABCD-1234" in body
        assert '"type": "success"' in body

    def test_login_unknown_backend_returns_400(self, client):
        resp = client.post(
            "/api/v1/external-agents/auth/login",
            json={"command": "totally-unknown-cli"},
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize("command", ["claude", "gemini", "qwen"])
    def test_a_login_the_user_runs_in_a_terminal_is_not_started_here(self, client, command):
        with patch("myrm_agent_harness.toolkits.acp.auth.CliLoginSession") as session:
            resp = client.post("/api/v1/external-agents/auth/login", json={"command": command})

        assert resp.status_code == 400
        assert "terminal" in resp.json()["detail"]
        session.assert_not_called()


def _detecting(*found: MagicMock) -> MagicMock:
    detector = MagicMock()
    detector.detect = AsyncMock(return_value=list(found))
    return detector


def _stand_in_claude(directory: Path) -> tuple[MagicMock, Path]:
    """A detected ``claude`` that reports a login once ``logged-in`` exists next to it, as the real one does after the user logs in."""
    cli = directory / "claude"
    marker = directory / "logged-in"
    cli.write_text(
        f'#!/bin/sh\nif [ -f \'{marker}\' ]; then echo \'{{"loggedIn": true, "authMethod": "claude.ai"}}\';'
        ' else echo \'{"loggedIn": false, "authMethod": "none"}\'; fi\n'
    )
    cli.chmod(0o755)
    found = MagicMock()
    found.name = "claude"
    found.path = str(cli)
    found.version = "2.0.0"
    return found, marker


class TestTerminalLogin:
    """POST /external-agents/auth/terminal-login."""

    def test_it_gives_the_command_for_each_shell_and_says_who_is_logged_in(self, client, tmp_path):
        found, _marker = _stand_in_claude(tmp_path)
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=_detecting(found),
        ):
            resp = client.post("/api/v1/external-agents/auth/terminal-login", json={"backend": "claude"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["backend"] == "claude"
        assert body["account"] is None
        assert body["authenticated"] is False
        assert body["authStatus"] == "not_authenticated"
        assert body["commands"]["posix"] == f"{shlex.quote(found.path)} auth login --claudeai"
        assert set(body["commands"]) == {"posix", "powershell", "cmd"}
        assert body["defaultShell"] in body["commands"]

    def test_asking_again_after_the_login_is_how_the_user_finds_that_it_worked(self, client, tmp_path):
        found, marker = _stand_in_claude(tmp_path)
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=_detecting(found),
        ):
            before = client.post("/api/v1/external-agents/auth/terminal-login", json={"backend": "claude"}).json()
            marker.touch()
            after = client.post("/api/v1/external-agents/auth/terminal-login", json={"backend": "claude"}).json()

        assert (before["authenticated"], after["authenticated"]) == (False, True)
        assert after["authStatus"] == "authenticated"

    def test_it_takes_and_stores_nothing(self, client, tmp_path):
        found, _marker = _stand_in_claude(tmp_path)
        before = sorted(tmp_path.rglob("*"))
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=_detecting(found),
        ):
            client.post("/api/v1/external-agents/auth/terminal-login", json={"backend": "claude"})

        assert sorted(tmp_path.rglob("*")) == before

    def test_an_agent_that_is_not_installed_has_no_login_to_run(self, client):
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=_detecting(),
        ):
            resp = client.post("/api/v1/external-agents/auth/terminal-login", json={"backend": "claude"})

        assert resp.status_code == 409
        assert resp.json()["detail"] == "claude is not installed"

    def test_an_unknown_agent_is_refused(self, client):
        resp = client.post("/api/v1/external-agents/auth/terminal-login", json={"backend": "no-such-backend"})

        assert resp.status_code == 400


class TestAuthLogout:
    """POST /external-agents/auth/logout."""

    def test_logout_removes_the_login_the_cli_left(self, client, tmp_path):
        cred = tmp_path / ".codex" / "auth.json"
        cred.parent.mkdir()
        cred.write_text('{"token": "abc"}', encoding="utf-8")

        resp = client.post(
            "/api/v1/external-agents/auth/logout",
            json={"backend": "codex"},
        )

        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False
        assert not cred.exists()


class TestTheHostTakesNoCredential:
    """A login is made by the CLI; no route accepts a credential to keep or a code to relay."""

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/auth/import", {"backend": "codex", "content": '{"token": "abc"}'}),
            ("/auth/login/s1/feed", {"text": "a-code"}),
        ],
    )
    def test_the_route_does_not_exist_and_nothing_is_written(self, client, tmp_path, path, body):
        resp = client.post(f"/api/v1/external-agents{path}", json=body)

        assert resp.status_code in {404, 405}
        assert list(tmp_path.iterdir()) == []
