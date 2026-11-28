"""The compact-chat endpoint starts a person's compaction and answers at once; the run has an endpoint of its own.

A compaction outlasts what a proxy lets one request wait, so ``POST /compact`` only accepts it and names the run, and the
page asks ``GET /compact/runs/{run_id}`` until it ends. What a run reports is what the service answered, with its own
reason, and each page reads the run it started.
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
from tests.support.compact_run_stub import compaction_answering, run_route, wait_for_run

_ROUTE = "/chat-1/compact"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    # One client for the whole test: the loop it runs the app on stays alive while a run goes on after its request.
    with TestClient(app) as test_client:
        yield test_client


def test_the_request_is_accepted_at_once_naming_the_run_that_is_running(client: TestClient) -> None:
    compaction = AsyncMock(return_value=CompactResult(compacted=True, message_count=12, tokens_saved=3400))

    with compaction_answering(compaction):
        response = client.post(_ROUTE, json={"focus_topic": "  billing  "})
        wait_for_run(client, response)

    run_id = response.json()["data"]["run_id"]
    assert response.status_code == 202
    assert isinstance(run_id, str)
    assert run_id
    assert response.json() == {"success": True, "code": 0, "data": {"run_id": run_id, "status": "running"}}


def test_the_compaction_is_a_persons_request_with_the_topic_trimmed(client: TestClient) -> None:
    compaction = AsyncMock(return_value=CompactResult(compacted=True, message_count=12, tokens_saved=3400))

    with compaction_answering(compaction):
        started = client.post(_ROUTE, json={"focus_topic": "  billing  "})
        wait_for_run(client, started)

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
        started = client.post(_ROUTE, json={"focus_topic": "billing"})
        data = wait_for_run(client, started)

    assert data == {
        "run_id": started.json()["data"]["run_id"],
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
        started = client.post(_ROUTE)
        data = wait_for_run(client, started)

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
        sent_at = time.monotonic()
        response = client.post(_ROUTE)
        answered_in = time.monotonic() - sent_at
        during = client.get(run_route(response))
        compaction.release.set()
        after = wait_for_run(client, response)

    assert response.status_code == 202
    # The compaction stays blocked for up to ten seconds: a request that waited for it would have answered late.
    assert answered_in < 5.0
    assert during.json()["data"] == {"run_id": response.json()["data"]["run_id"], "status": "running"}
    assert after["status"] == "succeeded"


def test_a_second_request_for_the_chat_joins_the_run_instead_of_compacting_twice(client: TestClient) -> None:
    compaction = _BlockedCompaction()

    with compaction_answering(compaction):
        first = client.post(_ROUTE)
        second = client.post(_ROUTE)
        compaction.release.set()
        wait_for_run(client, first)

    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json()["data"]["run_id"] == second.json()["data"]["run_id"]
    assert compaction.runs == 1


def test_the_next_request_after_a_finished_run_starts_a_new_one(client: TestClient) -> None:
    compaction = AsyncMock(return_value=CompactResult(compacted=True, tokens_saved=1))

    with compaction_answering(compaction):
        first = client.post(_ROUTE, json={"focus_topic": "first"})
        wait_for_run(client, first)
        second = client.post(_ROUTE, json={"focus_topic": "second"})
        data = wait_for_run(client, second)

    result = data["result"]
    assert isinstance(result, dict)
    assert result["focus_topic"] == "second"
    assert first.json()["data"]["run_id"] != second.json()["data"]["run_id"]
    assert compaction.await_count == 2


def test_a_page_reads_the_run_it_started_after_the_chats_next_run_has_ended(client: TestClient) -> None:
    """Two pages compact one chat one after the other; the first page's next poll comes after the second run ended."""
    compaction = AsyncMock(
        side_effect=[
            CompactResult(compacted=True, original_tokens=9000, summary_tokens=900, tokens_saved=8100, message_count=24),
            CompactResult(compacted=False, message_count=3, reason=CompactReason.TOO_FEW_MESSAGES),
        ]
    )

    with compaction_answering(compaction):
        first = client.post(_ROUTE, json={"focus_topic": "first"})
        wait_for_run(client, first)
        second = client.post(_ROUTE, json={"focus_topic": "second"})
        wait_for_run(client, second)
        first_page_polls = client.get(run_route(first))

    data = first_page_polls.json()["data"]
    assert first_page_polls.status_code == 200
    assert data["run_id"] == first.json()["data"]["run_id"]
    assert data["status"] == "succeeded"
    assert (data["result"]["compacted"], data["result"]["tokens_saved"], data["result"]["focus_topic"]) == (True, 8100, "first")


def test_a_run_the_server_does_not_hold_has_none_to_report(client: TestClient) -> None:
    response = client.get("/chat-1/compact/runs/never-started")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == BusinessCode.RESOURCE_NOT_FOUND.value


def test_a_run_is_reported_only_under_the_chat_it_belongs_to(client: TestClient) -> None:
    compaction = AsyncMock(return_value=CompactResult(compacted=True, tokens_saved=1))

    with compaction_answering(compaction):
        started = client.post(_ROUTE)
        wait_for_run(client, started)
        under_another_chat = client.get(f"/chat-2/compact/runs/{started.json()['data']['run_id']}")

    assert under_another_chat.status_code == 404
    assert under_another_chat.json()["detail"]["code"] == BusinessCode.RESOURCE_NOT_FOUND.value
