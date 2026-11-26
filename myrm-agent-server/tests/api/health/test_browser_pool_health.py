"""``GET /api/v1/health/browser`` reports the running browser pool and never creates one.

A probe is read-only, so it must not build the pool it reports on; a pool that has not been built yet reports what an
idle pool reports.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.support.minimal_app import build_minimal_app

app = build_minimal_app(preset="health")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@patch("myrm_agent_harness.toolkits.browser.pool.peek_global_browser_pool")
def test_browser_health_returns_pool_status(
    mock_pool_get: object,
    client: TestClient,
) -> None:
    """GET /browser must surface the pool health dict verbatim."""
    mock_pool = MagicMock()
    mock_pool.health = AsyncMock(
        return_value={
            "status": "healthy",
            "active": 2,
            "total": 4,
        }
    )
    mock_pool_get.return_value = mock_pool  # type: ignore[attr-defined]

    response = client.get("/api/v1/health/browser")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "active": 2, "total": 4}


@patch("myrm_agent_harness.toolkits.browser.pool.peek_global_browser_pool")
def test_browser_health_degrades_on_exception(
    mock_pool_get: object,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GET /browser must degrade to unhealthy when pool.health raises."""
    mock_pool = MagicMock()
    mock_pool.health = AsyncMock(side_effect=RuntimeError("pool exploded"))
    mock_pool_get.return_value = mock_pool  # type: ignore[attr-defined]

    with caplog.at_level(logging.WARNING, logger="app.api.health.browser"):
        response = client.get("/api/v1/health/browser")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unhealthy"
    assert "pool exploded" in data["error"]
    assert "Failed to get browser pool health" in data["message"]
    assert any(record.exc_info is not None and "pool exploded" in str(record.exc_info[1]) for record in caplog.records)


@patch("myrm_agent_harness.toolkits.browser.pool.get_global_browser_pool")
@patch("myrm_agent_harness.toolkits.browser.pool.peek_global_browser_pool", return_value=None)
def test_browser_health_reports_an_unbuilt_pool_as_idle_without_building_one(
    mock_peek: MagicMock,
    mock_build: MagicMock,
    client: TestClient,
) -> None:
    """A read-only probe must not create the pool, and an unbuilt pool is no problem to report."""
    response = client.get("/api/v1/health/browser")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "browsers_alive": 0,
        "browsers_total": 0,
        "message": "Browser pool has not started",
    }
    mock_build.assert_not_called()
