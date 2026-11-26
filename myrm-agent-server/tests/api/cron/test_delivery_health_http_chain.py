"""The status of a delivery destination over real HTTP deliveries, from a reminder's run to the route's answer.

[INPUT]
- app.api.cron.routes::router (POS: the whole cron router, which serves both the verdict route and the test-delivery route)
- app.core.cron.adapters.channel_delivery::ChannelResultDelivery (POS: the production delivery pipeline, webhook included)
- app.core.cron.adapters.sqlalchemy_store::SqlAlchemyCronStore (POS: the SQL store the jobs and their run records go through)
- myrm_agent_harness.toolkits.cron.engine.executor::JobExecutor (POS: runs a job, delivers its result and writes the run record)
- tests.support.policy_sqlite::policy_database (POS: a file SQLite database that runs the production transaction policy)

[OUTPUT]
- What a destination answers sets its status: an answer is healthy, a failure degrades it, three failures in a row
  take it down, one refused credential takes it down at once, and a delivery that goes through ends the failures
- Retries of one delivery are one failure, not several; a refused credential is not retried at all
- An address inside the network is refused by default, before any request is sent, and takes the destination down
- Jobs that deliver to one address read one status; an address nothing was delivered to has none
- A test delivery reaches the address but leaves the status exactly as it was, whether it goes through or not

[POS]
test_delivery_health_service.py covers the rules over stored rows and test_delivery_health_api.py the route's mapping
over a stand-in service. Here nothing between the reminder and the route's answer is replaced: the executor delivers
through the production pipeline to an endpoint that listens on loopback, the run records land in a real database, and
the answers are read through the real route.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from myrm_agent_harness.toolkits.cron import (
    CronJob,
    DeliveryConfig,
    JobType,
    NotificationRunner,
    Schedule,
    ScheduleKind,
)
from myrm_agent_harness.toolkits.cron.engine.executor import JobExecutor

from app.api.cron.routes import helpers
from app.api.cron.routes import router as cron_router
from app.core.cron.adapters.channel_delivery import ChannelResultDelivery
from app.core.cron.adapters.sqlalchemy_store import SqlAlchemyCronStore
from tests.support.policy_sqlite import policy_database

Verdict = dict[str, object]


class _Receiver:
    """A webhook endpoint on loopback that answers every POST with ``status`` and counts what reached it."""

    def __init__(self) -> None:
        self.status = 200
        self.requests = 0
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                receiver.requests += 1
                self.send_response(receiver.status)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self, path: str = "/hook") -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}{path}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@dataclass(frozen=True, slots=True)
class _Chain:
    store: SqlAlchemyCronStore
    executor: JobExecutor
    client: httpx.AsyncClient
    receiver: _Receiver

    async def add_reminder(self, job_id: str, path: str = "/hook") -> None:
        await self.store.save_job(
            CronJob(
                id=job_id,
                name=job_id,
                job_type=JobType.REMINDER,
                prompt="Delivery health probe",
                schedule=Schedule(kind=ScheduleKind.INTERVAL, interval_ms=3_600_000),
                delivery=DeliveryConfig(channel="webhook", target=self.receiver.url(path)),
            )
        )

    async def run(self, job_id: str) -> None:
        job = await self.store.get_job(job_id)
        assert job is not None
        await self.executor.run_and_record(job, NotificationRunner())

    async def verdict(self, job_id: str) -> Verdict | None:
        response = await self.client.get(f"/cron/{job_id}/delivery-health")
        if response.status_code == 204:
            return None
        assert response.status_code == 200, response.text
        verdict: Verdict = response.json()
        return verdict


def _attempt_at(verdict: Verdict | None) -> datetime:
    assert verdict is not None
    return datetime.fromisoformat(str(verdict["last_attempt_at"]))


@pytest.fixture
async def chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Chain]:
    # A failed delivery is retried after a pause that would only slow the test down; how often it is tried stays.
    monkeypatch.setattr("myrm_agent_harness.toolkits.cron.delivery._BACKOFF_BASE_S", 0.0)
    receiver = _Receiver()
    async with policy_database(tmp_path / "delivery_health_chain.db", monkeypatch) as db:
        # ``UnitOfWork`` takes its factory from ``app.platform_utils``, which ``policy_database`` does not redirect
        monkeypatch.setattr("app.database.repositories.uow.get_session_factory", lambda: db.factory)
        store = SqlAlchemyCronStore()
        monkeypatch.setattr(helpers, "_get_manager", lambda: SimpleNamespace(get_job=store.get_job))
        app = FastAPI()
        app.include_router(cron_router, prefix="/cron")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cron") as client:
            yield _Chain(store, JobExecutor(store=store, delivery=ChannelResultDelivery()), client, receiver)
    receiver.close()


@pytest.fixture
def loopback_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator's way to let the application deliver to an address inside its own network."""
    monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")


@pytest.mark.usefixtures("loopback_allowed")
async def test_what_a_destination_answers_sets_its_status_and_a_delivery_ends_the_failures(chain: _Chain) -> None:
    await chain.add_reminder("probe")
    started = datetime.now(UTC)

    await chain.run("probe")
    healthy = await chain.verdict("probe")
    assert healthy is not None
    assert (healthy["status"], healthy["failure_streak"], healthy["last_error_category"]) == ("healthy", 0, None)
    assert started <= _attempt_at(healthy) <= datetime.now(UTC)  # the time of the run that was just made

    chain.receiver.status = 500
    sent_before = chain.receiver.requests
    await chain.run("probe")
    tries_of_a_failed_run = chain.receiver.requests - sent_before
    assert tries_of_a_failed_run > 1  # a failed delivery is retried, which is what must not count as more failures
    degraded = await chain.verdict("probe")
    assert degraded is not None
    assert (degraded["status"], degraded["failure_streak"]) == ("degraded", 1)
    assert degraded["last_error_category"] == "http_server_error"
    assert _attempt_at(degraded) > _attempt_at(healthy)

    await chain.run("probe")
    await chain.run("probe")
    down = await chain.verdict("probe")
    assert down is not None
    assert (down["status"], down["failure_streak"]) == ("down", 3)  # three runs, however often each was tried
    assert chain.receiver.requests == 1 + 3 * tries_of_a_failed_run

    chain.receiver.status = 200
    await chain.run("probe")
    recovered = await chain.verdict("probe")
    assert recovered is not None
    assert (recovered["status"], recovered["failure_streak"], recovered["last_error_category"]) == ("healthy", 0, None)
    assert _attempt_at(recovered) > _attempt_at(down)


@pytest.mark.usefixtures("loopback_allowed")
async def test_a_refused_credential_takes_the_destination_down_at_once_without_a_retry(chain: _Chain) -> None:
    chain.receiver.status = 401
    await chain.add_reminder("probe")

    await chain.run("probe")

    verdict = await chain.verdict("probe")
    assert verdict is not None
    assert (verdict["status"], verdict["failure_streak"]) == ("down", 1)
    assert verdict["last_error_category"] == "auth_failure"
    assert chain.receiver.requests == 1


async def test_an_address_inside_the_network_is_refused_before_anything_is_sent(
    chain: _Chain, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MYRM_ALLOWED_INTERNAL_HOSTS", raising=False)
    monkeypatch.delenv("MYRM_ENABLE_SSRF_SHIELD", raising=False)
    await chain.add_reminder("probe")

    await chain.run("probe")

    verdict = await chain.verdict("probe")
    assert verdict is not None
    assert (verdict["status"], verdict["failure_streak"]) == ("down", 1)
    assert verdict["last_error_category"] == "address_rejected"
    assert chain.receiver.requests == 0


@pytest.mark.usefixtures("loopback_allowed")
async def test_jobs_delivering_to_one_address_read_one_status_and_another_address_has_none(chain: _Chain) -> None:
    chain.receiver.status = 500
    await chain.add_reminder("ran", "/shared")
    await chain.add_reminder("never-ran", "/shared")
    await chain.add_reminder("elsewhere", "/other")

    await chain.run("ran")

    ran = await chain.verdict("ran")
    assert ran is not None and ran["status"] == "degraded"
    assert await chain.verdict("never-ran") == ran
    assert await chain.verdict("elsewhere") is None


@pytest.mark.usefixtures("loopback_allowed")
async def test_a_test_delivery_reaches_the_address_but_never_changes_the_status(chain: _Chain) -> None:
    chain.receiver.status = 500
    await chain.add_reminder("probe")
    await chain.run("probe")
    failing = await chain.verdict("probe")
    assert failing is not None and failing["status"] == "degraded"

    chain.receiver.status = 200
    sent_before = chain.receiver.requests
    passed = await chain.client.post("/cron/probe/test-delivery")
    assert (passed.status_code, passed.json()) == (200, {"delivered": True})
    assert chain.receiver.requests == sent_before + 1  # the test went out; it was not skipped
    assert await chain.verdict("probe") == failing

    chain.receiver.status = 500
    failed = await chain.client.post("/cron/probe/test-delivery")
    assert failed.status_code == 502
    assert await chain.verdict("probe") == failing

    chain.receiver.status = 200
    await chain.run("probe")  # only a real delivery moves the status
    recovered = await chain.verdict("probe")
    assert recovered is not None and recovered["status"] == "healthy"
