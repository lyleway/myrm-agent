"""A managed sandbox never starts a subscription login or stores a subscription credential.

[POS]
Tests for app/api/external_agents/ in a sandbox deployment: login, feed and import are refused before anything runs
or is written, the status rows say so, and logout still removes a credential left over from before.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.external_agents._deploy_capability import require_subscription_login_capability
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
                json={"command": "codex", "backend": "codex", "sessionId": "s1"},
            )

        assert resp.status_code == 403
        assert "hosted deployments" in resp.json()["detail"]
        session.assert_not_called()

    def test_feed_is_refused(self, client, sandbox) -> None:
        resp = client.post(f"{_API}/auth/login/s1/feed", json={"text": "code"})

        assert resp.status_code == 403

    @pytest.mark.parametrize("backend", ["codex", "claude", "gemini", "qwen"])
    def test_import_is_refused_and_nothing_is_written(self, client, sandbox, tmp_path, backend: str) -> None:
        resp = client.post(f"{_API}/auth/import", json={"backend": backend, "content": '{"token": "x"}'})

        assert resp.status_code == 403
        assert list(tmp_path.iterdir()) == []

    def test_the_credential_store_refuses_on_its_own_when_the_route_gate_is_absent(self, client, sandbox, tmp_path) -> None:
        """Defence in depth: the store is built from the capability, so a route exposed without the gate still stores nothing."""
        app.dependency_overrides[require_subscription_login_capability] = lambda: None
        try:
            resp = client.post(f"{_API}/auth/import", json={"backend": "codex", "content": '{"token": "x"}'})
        finally:
            app.dependency_overrides.pop(require_subscription_login_capability, None)

        assert resp.status_code == 403
        assert "does not store subscription credentials" in resp.json()["detail"]
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
    def test_import_stores_the_credential(self, client, monkeypatch, tmp_path, mode: str) -> None:
        _use_deploy_mode(monkeypatch, mode)

        resp = client.post(f"{_API}/auth/import", json={"backend": "codex", "content": '{"token": "x"}'})

        assert resp.status_code == 200
        assert resp.json()["authenticated"] is True
        assert (tmp_path / ".codex" / "auth.json").is_file()
