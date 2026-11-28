"""The compact-chat endpoint starts a person's compaction and answers at once; the run has an endpoint of its own.

A compaction outlasts what a proxy lets one request wait, so ``POST /compact`` only accepts it and the page asks
``GET /compact/run`` until it ends. What a run reports is what the service answered, with its own reason.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chats.chat.compaction import router
from app.schemas.responses import BusinessCode
from app.services.chat.compact_service import CompactReason, CompactResult
from tests.support.compact_run_stub import RUN_ROUTE, compaction_answering, wait_for_run

_ROUTE = "/chat-1/compact"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    # One client for the whole test: the loop it runs the app on stays alive while a run goes on after its request.
    with TestClient(app) as test_client:
        yield test_client


def test_the_request_is_accepted_at_once_with_the_run_running(client: TestClient) -> None:
    compaction = AsyncMock(return_value=CompactResult(compacted=True, message_count=12, tokens_saved=3400))

    with compaction_answering(compaction):
        response = client.post(_ROUTE, json={"focus_topic": "  billing  "})
        wait_for_run(client)

    assert response.status_code == 202
    assert response.json() == {"success": True, "code": 0, "data": {"status": "running"}}


def test_the_compaction_is_a_persons_request_with_the_topic_trimmed(client: TestClient) -> None:
    compaction = AsyncMock(return_value=CompactResult(compacted=True, message_count=12, tokens_saved=3400))

    with compaction_answering(compaction):
        client.post(_ROUTE, json={"focus_topic": "  billing  "})
        wait_for_run(client)

    compaction.assert_awaited_once_with(None, "chat-1", focus_topic="billing", explicit_request=True)


def test_a_finished_run_reports_what_the_service_answered(client: TestClient) -> None:
    compaction = AsyncMock(
        return_value=CompactResult(
            compacted=True,
            original_tokens=9000,
            summary_tokens=900,
            tokens_saved=8100,
            message_count=24,
            backup_path="/tmp/backup.json",
        )
    )

    with compaction_answering(compaction):
        client.post(_ROUTE, json={"focus_topic": "billing"})
        data = wait_for_run(client)

    assert data == {
        "status": "succeeded",
        "result": {
            "compacted": True,
            "original_tokens": 9000,
            "summary_tokens": 900,
            "tokens_saved": 8100,
            "message_count": 24,
            "backup_path": "/tmp/backup.json",
            "reason": None,
            "focus_topic": "billing",
        },
    }


@pytest.mark.parametrize("reason", list(CompactReason))
def test_a_compaction_that_did_not_happen_reports_the_bare_reason_code(client: TestClient, reason: CompactReason) -> None:
    compaction = AsyncMock(return_value=CompactResult(compacted=False, message_count=3, reason=reason))

    with compaction_answering(compaction):
        client.post(_ROUTE)
        data = wait_for_run(client)

    result = data["result"]
    assert isinstance(result, dict)
    assert (data["status"], result["compacted"], result["reason"]) == ("succeeded", False, reason.value)


class _BlockedCompaction:
    """A compaction that ends when the test lets it (or, at the latest, after ten seconds), whichever comes first."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.runs = 0

    async def __call__(self, *_args: object, **_kwargs: object) -> CompactResult:
        self.runs += 1
        await asyncio.to_thread(self.release.wait, 10.0)
        return CompactResult(compacted=True, tokens_saved=1)


def test_a_run_reads_running_while_the_compaction_goes_on(client: TestClient) -> None:
    compaction = _BlockedCompaction()

    with compaction_answering(compaction):
        started = time.monotonic()
        response = client.post(_ROUTE)
        answered_in = time.monotonic() - started
        during = client.get(RUN_ROUTE)
        compaction.release.set()
        after = wait_for_run(client)

    assert response.status_code == 202
    # The compaction stays blocked for up to ten seconds: a request that waited for it would have answered late.
    assert answered_in < 5.0
    assert during.json()["data"] == {"status": "running"}
    assert after["status"] == "succeeded"


def test_a_second_request_for_the_chat_joins_the_run_instead_of_compacting_twice(client: TestClient) -> None:
    compaction = _BlockedCompaction()

    with compaction_answering(compaction):
        first = client.post(_ROUTE)
        second = client.post(_ROUTE)
        compaction.release.set()
        wait_for_run(client)

    assert (first.status_code, second.status_code) == (202, 202)
    assert compaction.runs == 1


def test_the_next_request_after_a_finished_run_starts_a_new_one(client: TestClient) -> None:
    compaction = AsyncMock(return_value=CompactResult(compacted=True, tokens_saved=1))

    with compaction_answering(compaction):
        client.post(_ROUTE, json={"focus_topic": "first"})
        wait_for_run(client)
        client.post(_ROUTE, json={"focus_topic": "second"})
        data = wait_for_run(client)

    result = data["result"]
    assert isinstance(result, dict)
    assert result["focus_topic"] == "second"
    assert compaction.await_count == 2


def test_a_chat_that_never_started_a_run_has_none_to_report(client: TestClient) -> None:
    response = client.get("/chat-never-compacted/compact/run")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == BusinessCode.RESOURCE_NOT_FOUND.value
