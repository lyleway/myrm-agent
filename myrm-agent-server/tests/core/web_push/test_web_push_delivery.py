"""WebPushService._send_one through the real pywebpush; only the outbound HTTP POST is stubbed.

The mocked-library tests in test_web_push_service.py classify push-service replies, but they never hand the
stored VAPID key to pywebpush. These tests do, so a key format pywebpush rejects fails here.
"""

from __future__ import annotations

import base64
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid.jwt import decode as decode_vapid_token

from app.core.brand import NOREPLY_EMAIL
from app.core.web_push.service import WebPushService

_PUSH_ORIGIN = "https://push.example.test"
_ENDPOINT = f"{_PUSH_ORIGIN}/send/abc"
_PAYLOAD = '{"title":"T","body":"B","url":"/"}'


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _browser_subscription_keys() -> tuple[str, str]:
    """(p256dh, auth) as a browser reports them: an uncompressed P-256 point and 16 random bytes, URL-safe base64."""
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return _b64url(point), _b64url(os.urandom(16))


def _push_service_reply(status_code: int) -> MagicMock:
    return MagicMock(status_code=status_code, text="", headers={})


async def _send(service: WebPushService, endpoint_hash: str = "h") -> bool:
    p256dh, auth = _browser_subscription_keys()
    return await service._send_one(endpoint=_ENDPOINT, p256dh=p256dh, auth=auth, payload=_PAYLOAD, endpoint_hash=endpoint_hash)


class TestSendOneAgainstRealLibrary:
    async def test_request_is_signed_with_the_advertised_vapid_key(self, keyed_service: WebPushService) -> None:
        with patch("pywebpush.requests.post", return_value=_push_service_reply(201)) as post:
            delivered = await _send(keyed_service)

        assert delivered is True
        post.assert_called_once()
        assert post.call_args.args[0] == _ENDPOINT
        headers = post.call_args.kwargs["headers"]
        assert headers["content-encoding"] == "aes128gcm"
        assert post.call_args.kwargs["data"]

        scheme, _, params = headers["authorization"].partition(" ")
        fields = dict(field.split("=", 1) for field in params.split(","))
        assert scheme == "vapid"
        assert fields["k"] == keyed_service.public_key
        # Raises InvalidSignature unless the token was signed by the key browsers subscribed against.
        claims = decode_vapid_token(fields["t"], keyed_service.public_key)
        assert claims["aud"] == _PUSH_ORIGIN
        assert claims["sub"] == f"mailto:{NOREPLY_EMAIL}"
        assert claims["exp"] > time.time()

    async def test_request_has_a_bounded_timeout(self, keyed_service: WebPushService) -> None:
        with patch("pywebpush.requests.post", return_value=_push_service_reply(201)) as post:
            await _send(keyed_service)

        timeout = post.call_args.kwargs["timeout"]
        assert timeout is not None
        assert 0 < timeout <= 30

    @pytest.mark.parametrize(("status_code", "subscription_removed"), [(410, True), (404, True), (500, False)])
    async def test_push_service_reply_decides_whether_the_subscription_is_removed(
        self, keyed_service: WebPushService, status_code: int, subscription_removed: bool
    ) -> None:
        with (
            patch("pywebpush.requests.post", return_value=_push_service_reply(status_code)),
            patch.object(keyed_service, "_remove_subscription", new_callable=AsyncMock) as remove,
        ):
            delivered = await _send(keyed_service, endpoint_hash="h_reply")

        assert delivered is False
        if subscription_removed:
            remove.assert_awaited_once_with("h_reply")
        else:
            remove.assert_not_awaited()
