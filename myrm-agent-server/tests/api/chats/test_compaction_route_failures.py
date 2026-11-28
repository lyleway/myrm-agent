"""A failed summary model behind a compaction run is reported by what the provider answered.

The run turns a ``ValueError`` into "invalid request" (400) and everything else into its catch-all (500). A provider's
failure is not the caller's invalid input, so an unreadable provider answer takes the catch-all too. The request that
started the run was accepted and is long gone, so the failure is what the run endpoint reports, with the status the
failure would have had as a response.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from myrm_agent_harness.toolkits.llms.transport.errors import LLMResponseError

from app.api.chats.chat.compaction import router
from app.schemas.responses import BusinessCode
from tests.support.compact_run_stub import compaction_answering, wait_for_run
from tests.support.provider_failures import provider_failure

_SECRET = "sk-live-0123456789abcdef"
_REJECTED = f"{_SECRET} is not valid for org-acme"
_ROUTE = "/chat-1/compact"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (provider_failure(401, _REJECTED), BusinessCode.AI_AUTH_ERROR),
        (provider_failure(429, _REJECTED), BusinessCode.AI_RATE_LIMIT_ERROR),
        (provider_failure(503, _REJECTED), BusinessCode.AI_MODEL_ERROR),
        (LLMResponseError("openai", "openai answered with a non-JSON body: '<html>'"), BusinessCode.AI_MODEL_ERROR),
        (httpx.ConnectTimeout("provider unreachable"), BusinessCode.AI_TIMEOUT_ERROR),
    ],
)
def test_a_failed_summary_model_is_a_500_named_by_what_the_provider_answered(
    client: TestClient, failure: Exception, code: BusinessCode
) -> None:
    with compaction_answering(AsyncMock(side_effect=failure)):
        started = client.post(_ROUTE)
        data = wait_for_run(client)
        reported = client.get("/chat-1/compact/run")

    assert started.status_code == 202
    error = data["error"]
    assert isinstance(error, dict)
    assert data["status"] == "failed"
    assert (error["http_status"], error["code"]) == (500, code.value)
    assert str(error["message"]).startswith("Compact chat context failed: AI ")
    assert _SECRET not in started.text
    assert _SECRET not in reported.text


def test_an_invalid_request_is_still_a_400(client: TestClient) -> None:
    with compaction_answering(AsyncMock(side_effect=ValueError("Cannot resolve before_message_id for chat_id=chat-1"))):
        client.post(_ROUTE)
        data = wait_for_run(client)

    error = data["error"]
    assert isinstance(error, dict)
    assert data["status"] == "failed"
    assert (error["http_status"], error["code"]) == (400, BusinessCode.VALIDATION_ERROR.value)
