"""Tests for WebPushService._send_one — how a push-service reply or a network error becomes the reason a push missed.

pywebpush's call is mocked here; test_web_push_delivery.py runs the real library.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pywebpush import WebPushException

from app.core.web_push.service import WebPushService


class TestSendOne:
    @pytest.mark.asyncio
    async def test_successful_send(self, keyed_service: WebPushService) -> None:
        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await keyed_service._send_one(
                endpoint="https://e.com",
                p256dh="k",
                auth="a",
                payload='{"title":"T"}',
                endpoint_hash="h1",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_removes_gone_subscription_on_410(self, keyed_service: WebPushService) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 410

        exc = WebPushException("gone", response=mock_response)

        with (
            patch(
                "asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=exc,
            ),
            patch.object(keyed_service, "_remove_subscription", new_callable=AsyncMock) as mock_remove,
        ):
            result = await keyed_service._send_one(
                endpoint="https://e.com",
                p256dh="k",
                auth="a",
                payload="{}",
                endpoint_hash="h_gone",
            )

        assert result == "HTTP 410 (subscription removed)"
        mock_remove.assert_called_once_with("h_gone")

    @pytest.mark.asyncio
    async def test_removes_gone_subscription_on_404(self, keyed_service: WebPushService) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404

        exc = WebPushException("not found", response=mock_response)

        with (
            patch(
                "asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=exc,
            ),
            patch.object(keyed_service, "_remove_subscription", new_callable=AsyncMock) as mock_remove,
        ):
            result = await keyed_service._send_one(
                endpoint="https://e.com",
                p256dh="k",
                auth="a",
                payload="{}",
                endpoint_hash="h_404",
            )

        assert result == "HTTP 404 (subscription removed)"
        mock_remove.assert_called_once_with("h_404")

    @pytest.mark.asyncio
    async def test_reason_is_the_error_type_on_generic_exception(self, keyed_service: WebPushService) -> None:
        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=ConnectionError("network"),
        ):
            result = await keyed_service._send_one(
                endpoint="https://e.com",
                p256dh="k",
                auth="a",
                payload="{}",
                endpoint_hash="h_err",
            )

        assert result == "ConnectionError"

    @pytest.mark.asyncio
    async def test_reason_is_the_error_type_on_webpush_exception_without_response(self, keyed_service: WebPushService) -> None:
        exc = WebPushException("server error")

        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=exc,
        ):
            result = await keyed_service._send_one(
                endpoint="https://e.com",
                p256dh="k",
                auth="a",
                payload="{}",
                endpoint_hash="h_no_resp",
            )

        assert result == "WebPushException"

    @pytest.mark.asyncio
    async def test_webpush_exception_with_non_gone_status(self, keyed_service: WebPushService) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500

        exc = WebPushException("server error", response=mock_response)

        with (
            patch(
                "asyncio.to_thread",
                new_callable=AsyncMock,
                side_effect=exc,
            ),
            patch.object(keyed_service, "_remove_subscription", new_callable=AsyncMock) as mock_remove,
        ):
            result = await keyed_service._send_one(
                endpoint="https://e.com",
                p256dh="k",
                auth="a",
                payload="{}",
                endpoint_hash="h_500",
            )

        assert result == "HTTP 500"
        mock_remove.assert_not_called()
