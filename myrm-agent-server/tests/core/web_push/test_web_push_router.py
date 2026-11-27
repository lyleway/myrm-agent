"""POST /web-push/test: the response says how many devices the test reached and why the others were missed."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.web_push.router import router, send_test
from app.api.web_push.schemas import WebPushTestRequest, WebPushTestResponse
from app.core.web_push.service import BroadcastResult

_NO_DEVICE = "No device is registered for Web Push on this server."


def _service_returning(result: BroadcastResult) -> MagicMock:
    return MagicMock(broadcast=AsyncMock(return_value=result))


async def _test_push(result: BroadcastResult) -> WebPushTestResponse:
    with patch("app.api.web_push.router.get_web_push_service", return_value=_service_returning(result)):
        return await send_test(WebPushTestRequest())


class TestSendTest:
    async def test_reached_every_device(self) -> None:
        response = await _test_push(BroadcastResult(subscriptions=2))

        assert response == WebPushTestResponse(delivered=2, subscriptions=2, failures=[])

    async def test_lists_each_reason_a_device_was_missed_once(self) -> None:
        result = BroadcastResult(
            subscriptions=3, failures=("HTTP 410 Gone (subscription removed)", "HTTP 410 Gone (subscription removed)")
        )

        response = await _test_push(result)

        assert response == WebPushTestResponse(delivered=1, subscriptions=3, failures=["HTTP 410 Gone (subscription removed)"])

    async def test_reaching_no_device_says_why(self) -> None:
        response = await _test_push(BroadcastResult(subscriptions=1, failures=("ConnectTimeout",)))

        assert response == WebPushTestResponse(delivered=0, subscriptions=1, failures=["ConnectTimeout"])

    async def test_no_registered_device_is_a_reason_of_its_own(self) -> None:
        response = await _test_push(BroadcastResult(subscriptions=0))

        assert response == WebPushTestResponse(delivered=0, subscriptions=0, failures=[_NO_DEVICE])

    async def test_forwards_the_request_to_the_broadcast(self) -> None:
        service = _service_returning(BroadcastResult(subscriptions=1))
        request = WebPushTestRequest(title="T", body="B", url="/chat-1")

        with patch("app.api.web_push.router.get_web_push_service", return_value=service):
            await send_test(request)

        service.broadcast.assert_awaited_once_with(title="T", body="B", url="/chat-1")


@pytest.fixture
def client() -> AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestWireFormat:
    async def test_body_carries_the_counts_and_the_reasons(self, client: AsyncClient) -> None:
        service = _service_returning(BroadcastResult(subscriptions=2, failures=("HTTP 403 Forbidden",)))

        with patch("app.api.web_push.router.get_web_push_service", return_value=service):
            async with client:
                response = await client.post("/web-push/test", json={})

        assert response.status_code == 200
        assert response.json() == {"delivered": 1, "subscriptions": 2, "failures": ["HTTP 403 Forbidden"]}
