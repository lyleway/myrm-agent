"""HTTP surface of installed artifact apps: install gate, grants, activity and the host fetch proxy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.artifact_apps import router as artifact_apps_router
from app.api.dependencies import get_workspace_root
from app.database.connection import get_db
from app.database.models import Artifact, ArtifactVersion, Base
from app.services.artifact_apps import fetch_proxy
from app.services.artifact_apps.rate_limit import FETCH_LIMITER

_SHA = "a" * 64
_NEWER_SHA = "b" * 64
_PAGE_REQUEST = {"version_sha256": _SHA, "url": "https://api.weather.com/v1/now", "method": "GET"}


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add_all(
            [
                Artifact(id="clock", name="clock.html", chat_id="chat-1"),
                Artifact(id="notes", name="notes.md"),
            ]
        )
        db.add_all(
            [
                ArtifactVersion(id="v1", artifact_id="clock", vault_uri="vault://o1", sha256_hash=_SHA),
                ArtifactVersion(id="v0", artifact_id="notes", vault_uri="vault://o2", sha256_hash=_SHA),
            ]
        )
        await db.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
async def client(session_factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(artifact_apps_router, prefix="/api/v1")

    async def database() -> AsyncIterator[AsyncSession]:
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_workspace_root] = lambda: tmp_path
    FETCH_LIMITER._starts.clear()
    FETCH_LIMITER._in_flight.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://apps.test") as http:
        yield http


async def _install(client: httpx.AsyncClient, artifact_id: str = "clock") -> httpx.Response:
    return await client.put(f"/api/v1/artifact-apps/{artifact_id}")


async def _grant(client: httpx.AsyncClient, capability: str, decision: str, scope: list[str]) -> httpx.Response:
    return await client.put(
        f"/api/v1/artifact-apps/clock/grants/{capability}",
        json={"decision": decision, "scope": scope, "version_sha256": _SHA},
    )


class _Upstream:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, client: httpx.AsyncClient, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append(url)
        return httpx.Response(200, content=b'{"temp": 21}', headers={"content-type": "application/json"})


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> _Upstream:
    fake = _Upstream()
    monkeypatch.setattr(fetch_proxy, "secure_request", fake)
    return fake


async def test_installing_an_html_artifact_returns_what_the_host_page_needs(client: httpx.AsyncClient) -> None:
    response = await _install(client)

    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["artifact_id"] == "clock"
    assert detail["chat_id"] == "chat-1"
    assert detail["latest_version"] == {"id": "v1", "vault_uri": "vault://o1", "sha256_hash": _SHA}
    assert detail["grants"] == []


async def test_only_html_artifacts_can_be_installed(client: httpx.AsyncClient) -> None:
    assert (await _install(client, "notes")).status_code == 400
    assert (await client.get("/api/v1/artifact-apps")).json()["data"] == []


async def test_an_unknown_artifact_cannot_be_installed(client: httpx.AsyncClient) -> None:
    assert (await _install(client, "missing")).status_code == 404


async def test_a_page_of_an_uninstalled_artifact_has_no_detail_grants_or_proxy(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/artifact-apps/clock")).status_code == 404
    assert (await _grant(client, "notify", "allow", [])).status_code == 404
    assert (await client.post("/api/v1/artifact-apps/clock/fetch", json=_PAGE_REQUEST)).status_code == 404


async def test_the_list_shows_installed_apps_and_uninstall_removes_them(client: httpx.AsyncClient) -> None:
    await _install(client)

    listed = (await client.get("/api/v1/artifact-apps")).json()["data"]
    assert [(app["artifact_id"], app["name"]) for app in listed] == [("clock", "clock.html")]

    assert (await client.delete("/api/v1/artifact-apps/clock")).status_code == 204
    assert (await client.get("/api/v1/artifact-apps")).json()["data"] == []


async def test_uninstalling_drops_the_grants_so_a_reinstall_asks_again(client: httpx.AsyncClient) -> None:
    await _install(client)
    await _grant(client, "notify", "allow", [])

    await client.delete("/api/v1/artifact-apps/clock")

    assert (await _install(client)).json()["data"]["grants"] == []


async def test_a_grant_is_stored_with_a_canonical_scope_and_shown_in_the_detail(client: httpx.AsyncClient) -> None:
    await _install(client)

    saved = await _grant(client, "fetch", "allow", ["API.Weather.com", "api.weather.com"])

    assert saved.json()["data"] == {
        "capability": "fetch",
        "decision": "allow",
        "scope": ["api.weather.com"],
        "version_sha256": _SHA,
    }
    detail = (await client.get("/api/v1/artifact-apps/clock")).json()["data"]
    assert [(g["capability"], g["decision"], g["scope"]) for g in detail["grants"]] == [("fetch", "allow", ["api.weather.com"])]


@pytest.mark.parametrize(
    ("capability", "scope"),
    [("fetch", ["127.0.0.1"]), ("fetch", []), ("fetch", ["*.example.com"]), ("notify", ["api.weather.com"])],
)
async def test_a_scope_the_capability_cannot_carry_is_refused(
    client: httpx.AsyncClient, capability: str, scope: list[str]
) -> None:
    await _install(client)

    assert (await _grant(client, capability, "allow", scope)).status_code == 422


async def test_an_unknown_capability_is_refused(client: httpx.AsyncClient) -> None:
    await _install(client)

    assert (await _grant(client, "camera", "allow", [])).status_code == 422


async def test_a_granted_host_is_fetched_and_the_call_is_logged_as_metadata_only(
    client: httpx.AsyncClient, upstream: _Upstream
) -> None:
    await _install(client)
    await _grant(client, "fetch", "allow", ["api.weather.com"])

    reply = (await client.post("/api/v1/artifact-apps/clock/fetch", json=_PAGE_REQUEST)).json()["data"]

    assert reply["ok"] is True
    assert (reply["status"], reply["body"]) == (200, '{"temp": 21}')
    assert upstream.calls == ["https://api.weather.com/v1/now"]
    (entry,) = (await client.get("/api/v1/artifact-apps/clock/activity")).json()["data"]
    assert entry["capability"] == "fetch"
    assert entry["outcome"] == "ok"
    assert entry["detail"] == "api.weather.com"
    assert set(entry) == {"capability", "outcome", "detail", "version_sha256", "created_at"}


async def test_an_ungranted_host_is_refused_without_a_request_and_logged_as_denied(
    client: httpx.AsyncClient, upstream: _Upstream
) -> None:
    await _install(client)
    await _grant(client, "fetch", "allow", ["api.weather.com"])

    reply = (
        await client.post(
            "/api/v1/artifact-apps/clock/fetch", json={**_PAGE_REQUEST, "url": "https://evil.example.com/x?secret=1"}
        )
    ).json()["data"]

    assert (reply["ok"], reply["code"]) == (False, "host_not_granted")
    assert upstream.calls == []
    (entry,) = (await client.get("/api/v1/artifact-apps/clock/activity")).json()["data"]
    assert (entry["outcome"], entry["detail"]) == ("denied", "evil.example.com")


async def test_a_denied_fetch_grant_blocks_every_host(client: httpx.AsyncClient, upstream: _Upstream) -> None:
    await _install(client)
    await _grant(client, "fetch", "deny", ["api.weather.com"])

    reply = (await client.post("/api/v1/artifact-apps/clock/fetch", json=_PAGE_REQUEST)).json()["data"]

    assert (reply["ok"], reply["code"]) == (False, "host_not_granted")
    assert upstream.calls == []


async def test_a_malformed_url_is_refused_and_logged_without_a_host(client: httpx.AsyncClient, upstream: _Upstream) -> None:
    await _install(client)

    reply = (
        await client.post("/api/v1/artifact-apps/clock/fetch", json={**_PAGE_REQUEST, "url": "http://api.weather.com/"})
    ).json()["data"]

    assert (reply["ok"], reply["code"]) == (False, "bad_request")
    (entry,) = (await client.get("/api/v1/artifact-apps/clock/activity")).json()["data"]
    assert (entry["outcome"], entry["detail"]) == ("error", None)


async def test_a_burst_over_the_rate_limit_is_refused_and_logged(client: httpx.AsyncClient, upstream: _Upstream) -> None:
    await _install(client)
    await _grant(client, "fetch", "allow", ["api.weather.com"])
    for _ in range(60):
        assert (await client.post("/api/v1/artifact-apps/clock/fetch", json=_PAGE_REQUEST)).json()["data"]["ok"]

    reply = (await client.post("/api/v1/artifact-apps/clock/fetch", json=_PAGE_REQUEST)).json()["data"]

    assert (reply["ok"], reply["code"]) == (False, "rate_limited")
    assert len(upstream.calls) == 60


async def test_the_host_reports_a_page_side_call_into_the_same_log(client: httpx.AsyncClient) -> None:
    await _install(client)

    reported = await client.post(
        "/api/v1/artifact-apps/clock/activity",
        json={"version_sha256": _NEWER_SHA, "capability": "notify", "outcome": "ok"},
    )

    assert reported.status_code == 204
    (entry,) = (await client.get("/api/v1/artifact-apps/clock/activity")).json()["data"]
    assert (entry["capability"], entry["outcome"], entry["version_sha256"]) == ("notify", "ok", _NEWER_SHA)


async def test_a_malformed_version_hash_is_refused(client: httpx.AsyncClient) -> None:
    await _install(client)

    reported = await client.post(
        "/api/v1/artifact-apps/clock/activity",
        json={"version_sha256": "not-a-hash", "capability": "notify", "outcome": "ok"},
    )

    assert reported.status_code == 422
