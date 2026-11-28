"""A managed sandbox never starts a subscription login, stores a subscription credential or runs a blocked agent.

[POS]
Tests for app/api/external_agents/ in a sandbox deployment: login and terminal login are refused before anything runs
or is written, the status rows say so, logout still removes a credential left over from before, and an agent the
deployment does not run is neither installed nor probed and is reported as not available.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from myrm_agent_harness.toolkits.acp.auth import AuthStatus, CredentialState
from myrm_agent_harness.toolkits.acp.core.backend_detector import DetectedBackend

from app.config.deploy_mode import get_deploy_mode
from app.core.security.auth.identity import LOCAL_USER_ID
from app.platform_utils.deployment_capabilities import _reset_capabilities_cache_for_testing
from tests.support.minimal_app import build_minimal_app

app = build_minimal_app(preset="external_agents")

_API = "/api/v1/external-agents"


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
def _restore_deploy_mode() -> Iterator[None]:
    yield
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)
    original = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.router.lifespan_context = original


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_deploy_mode(monkeypatch, "sandbox")


def _no_cli_detected() -> MagicMock:
    detector = MagicMock()
    detector.detect = AsyncMock(return_value=[])
    return detector


class TestSandboxRefusesSubscriptionLogin:
    def test_login_is_refused_and_no_cli_is_started(self, client, sandbox) -> None:
        with patch("myrm_agent_harness.toolkits.acp.auth.CliLoginSession") as session:
            resp = client.post(
                f"{_API}/auth/login",
                json={"command": "codex", "backend": "codex"},
            )

        assert resp.status_code == 403
        assert "hosted deployments" in resp.json()["detail"]
        session.assert_not_called()

    @pytest.mark.parametrize("backend", ["codex", "claude", "gemini", "qwen"])
    def test_terminal_login_is_refused_and_nothing_is_made(self, client, sandbox, tmp_path, backend: str) -> None:
        resp = client.post(f"{_API}/auth/terminal-login", json={"backend": backend, "account": "work"})

        assert resp.status_code == 403
        assert "hosted deployments" in resp.json()["detail"]
        assert list(tmp_path.iterdir()) == []

    def test_status_rows_say_that_login_is_not_available(self, client, sandbox) -> None:
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=_no_cli_detected(),
        ):
            resp = client.get(f"{_API}/auth/status")

        assert resp.status_code == 200
        rows = resp.json()["backends"]
        assert len(rows) == 4
        assert all(row["subscriptionLoginAllowed"] is False for row in rows)

    def test_a_credential_left_from_before_can_still_be_removed(self, client, sandbox, tmp_path) -> None:
        leftover = tmp_path / ".codex" / "auth.json"
        leftover.parent.mkdir()
        leftover.write_text('{"token": "old"}', encoding="utf-8")

        resp = client.post(f"{_API}/auth/logout", json={"backend": "codex"})

        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False
        assert not leftover.exists()


def _detector_finding(*found: DetectedBackend) -> MagicMock:
    detector = MagicMock()
    detector.detect = AsyncMock(return_value=list(found))
    return detector


_ALL_FOUND = (
    DetectedBackend(name="claude", path="/bin/claude", version="2.1.0"),
    DetectedBackend(name="codex", path="/bin/codex", version="0.172.0"),
    DetectedBackend(name="gemini", path="/bin/gemini", version="0.3.0"),
    DetectedBackend(name="qwen", path="/bin/qwen", version="0.1.0"),
)


def _login_state_probe(probed: list[str]) -> AsyncMock:
    async def state(_store: object, backend: str, **_kwargs: object) -> CredentialState:
        probed.append(backend)
        return CredentialState(backend=backend, status=AuthStatus.AUTHENTICATED)

    return AsyncMock(side_effect=state)


def _install_events(response) -> list[dict[str, object]]:
    return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


class TestSandboxDoesNotRunBlockedAgents:
    @pytest.mark.parametrize("backend", ["claude", "codex", "claude-code"])
    def test_install_is_refused_before_anything_is_downloaded(self, client, sandbox, backend: str) -> None:
        with patch("myrm_agent_harness.toolkits.acp.toolchains.IsolatedToolchainManager") as manager:
            resp = client.post(f"{_API}/install/{backend}")

        assert resp.status_code == 403
        assert "not available in hosted deployments" in resp.json()["detail"]
        manager.assert_not_called()

    @pytest.mark.parametrize("backend", ["gemini", "qwen"])
    def test_the_other_agents_can_still_be_installed(self, client, sandbox, backend: str) -> None:
        class Manager:
            async def install_backend(self, _backend: str) -> AsyncIterator[str]:
                yield "downloading"

        with patch("myrm_agent_harness.toolkits.acp.toolchains.IsolatedToolchainManager", return_value=Manager()):
            with client.stream("POST", f"{_API}/install/{backend}") as resp:
                assert resp.status_code == 200
                events = _install_events(resp)

        assert events[-1]["type"] == "success"

    def test_status_reports_blocked_agents_as_absent_and_not_available_without_starting_them(self, client, sandbox) -> None:
        probed: list[str] = []
        with (
            patch(
                "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
                return_value=_detector_finding(*_ALL_FOUND),
            ),
            patch("myrm_agent_harness.toolkits.acp.auth.resolve_login_state", _login_state_probe(probed)),
        ):
            resp = client.get(f"{_API}/auth/status")

        rows = {row["backend"]: row for row in resp.json()["backends"]}
        assert set(rows) == {"claude", "codex", "gemini", "qwen"}
        for blocked in ("claude", "codex"):
            assert rows[blocked]["availableInDeployment"] is False
            assert rows[blocked]["installed"] is False
            assert rows[blocked]["path"] is None
            assert rows[blocked]["version"] is None
            assert rows[blocked]["authenticated"] is False
            assert rows[blocked]["authStatus"] == "not_authenticated"
        for allowed in ("gemini", "qwen"):
            assert rows[allowed]["availableInDeployment"] is True
            assert rows[allowed]["installed"] is True
            assert rows[allowed]["authenticated"] is True
        assert sorted(probed) == ["gemini", "qwen"]

    def test_the_catalog_marks_the_agents_the_deployment_does_not_run(self, client, sandbox) -> None:
        agents = {a["backend"]: a for a in client.get(f"{_API}/catalog").json()["agents"]}

        assert {name: a["availableInDeployment"] for name, a in agents.items()} == {
            "claude": False,
            "codex": False,
            "gemini": True,
            "qwen": True,
        }

    def test_status_still_shows_a_login_left_from_before_so_that_it_can_be_removed(self, client, sandbox, tmp_path) -> None:
        leftover = tmp_path / ".claude" / ".credentials.json"
        leftover.parent.mkdir()
        leftover.write_text('{"token": "old"}', encoding="utf-8")
        probed: list[str] = []
        with (
            patch(
                "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
                return_value=_detector_finding(*_ALL_FOUND),
            ),
            patch("myrm_agent_harness.toolkits.acp.auth.resolve_login_state", _login_state_probe(probed)),
        ):
            resp = client.get(f"{_API}/auth/status")

        claude = next(row for row in resp.json()["backends"] if row["backend"] == "claude")
        assert claude["authenticated"] is True
        assert claude["availableInDeployment"] is False
        assert "claude" not in probed


class TestLocalDeploymentKeepsSubscriptionLogin:
    @pytest.mark.parametrize("mode", ["local", "tauri"])
    def test_status_rows_say_that_login_is_available(self, client, monkeypatch, mode: str) -> None:
        _use_deploy_mode(monkeypatch, mode)
        with patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            return_value=_no_cli_detected(),
        ):
            resp = client.get(f"{_API}/auth/status")

        assert all(row["subscriptionLoginAllowed"] is True for row in resp.json()["backends"])

    @pytest.mark.parametrize("mode", ["local", "tauri"])
    def test_every_agent_is_available_and_probed(self, client, monkeypatch, mode: str) -> None:
        _use_deploy_mode(monkeypatch, mode)
        probed: list[str] = []
        with (
            patch(
                "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
                return_value=_detector_finding(*_ALL_FOUND),
            ),
            patch("myrm_agent_harness.toolkits.acp.auth.resolve_login_state", _login_state_probe(probed)),
        ):
            resp = client.get(f"{_API}/auth/status")

        rows = resp.json()["backends"]
        assert all(row["availableInDeployment"] is True and row["installed"] is True for row in rows)
        assert sorted(probed) == ["claude", "codex", "gemini", "qwen"]

    @pytest.mark.parametrize("backend", ["claude", "codex"])
    def test_every_agent_can_be_installed(self, client, monkeypatch, backend: str) -> None:
        _use_deploy_mode(monkeypatch, "local")

        class Manager:
            async def install_backend(self, _backend: str) -> AsyncIterator[str]:
                yield "downloading"

        with patch("myrm_agent_harness.toolkits.acp.toolchains.IsolatedToolchainManager", return_value=Manager()):
            with client.stream("POST", f"{_API}/install/{backend}") as resp:
                assert resp.status_code == 200
                events = _install_events(resp)

        assert events[-1]["type"] == "success"

    @pytest.mark.parametrize("mode", ["local", "tauri"])
    def test_the_login_a_user_runs_in_a_terminal_is_described(self, client, monkeypatch, mode: str) -> None:
        _use_deploy_mode(monkeypatch, mode)
        probed: list[str] = []
        with (
            patch(
                "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
                return_value=_detector_finding(*_ALL_FOUND),
            ),
            patch("myrm_agent_harness.toolkits.acp.auth.resolve_login_state", _login_state_probe(probed)),
        ):
            resp = client.post(f"{_API}/auth/terminal-login", json={"backend": "gemini"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["authenticated"] is True
        assert body["commands"]["posix"] == "/bin/gemini"
        assert probed == ["gemini"]
