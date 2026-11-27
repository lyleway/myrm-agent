"""Web Push service — subscription CRUD + push sending.

[INPUT]
- app.core.web_push.vapid_keys::load_vapid_keys
- pywebpush::Vapid, WebPushException, webpush (POS: VAPID signing and the encrypted push request)
- app.database.models::WebPushSubscription
- app.database.connection::get_session
- app.core.brand::NOREPLY_EMAIL (POS: the VAPID contact address)

[OUTPUT]
- WebPushService: subscribe/unsubscribe/broadcast
- BroadcastResult: what one broadcast reached, with a reason for every subscription it missed
- get_web_push_service(): singleton accessor

[POS]
Core Web Push business logic. Decoupled from API layer and event dispatch.
Uses pywebpush for VAPID-signed push, SQLAlchemy for subscription persistence.
A push that fails never raises: it comes back as a reason, so callers can tell the user why.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update

from app.core.brand import NOREPLY_EMAIL

logger = logging.getLogger(__name__)

_GONE_STATUS_CODES = frozenset({404, 410})

# requests treats timeout=None as "wait forever", which would pin an executor thread on a stalled push service.
_PUSH_REQUEST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class BroadcastResult:
    """What one broadcast reached: ``failures`` holds the reason for each registered subscription it missed."""

    subscriptions: int
    failures: tuple[str, ...] = ()

    @property
    def delivered(self) -> int:
        return self.subscriptions - len(self.failures)

    @property
    def reasons(self) -> tuple[str, ...]:
        """The distinct failure reasons, in the order they first occurred."""
        return tuple(dict.fromkeys(self.failures))


def _failure_reason(exc: Exception) -> str:
    """Why a push failed, worded so it can be shown: the push service's HTTP reply, else the type of the error.

    The error message is left out on purpose: requests puts the whole request URL into it, and a push endpoint URL
    identifies the subscriber's device.
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        return type(exc).__name__
    phrase = getattr(response, "reason", None)
    return f"HTTP {status} {phrase}" if isinstance(phrase, str) and phrase else f"HTTP {status}"


class WebPushService:
    """Manages Web Push subscriptions and sends VAPID-signed push messages."""

    def __init__(self) -> None:
        self._private_pem: str | None = None
        self._public_key: str | None = None

    def _ensure_keys(self) -> tuple[str, str]:
        if self._private_pem is None or self._public_key is None:
            from app.core.web_push.vapid_keys import load_vapid_keys

            self._private_pem, self._public_key = load_vapid_keys()
        return self._private_pem, self._public_key

    @property
    def public_key(self) -> str:
        """Application server public key (URL-safe base64, no padding)."""
        _, pub = self._ensure_keys()
        return pub

    @staticmethod
    def _hash_endpoint(endpoint: str) -> str:
        return hashlib.sha256(endpoint.encode()).hexdigest()[:32]

    async def subscribe(
        self,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str = "",
    ) -> str:
        """Register or update a push subscription. Returns endpoint_hash."""
        from app.database.connection import get_session
        from app.database.models import WebPushSubscription

        endpoint_hash = self._hash_endpoint(endpoint)
        now = datetime.now(timezone.utc)

        async with get_session() as session:
            existing = (
                await session.execute(select(WebPushSubscription).where(WebPushSubscription.endpoint_hash == endpoint_hash))
            ).scalar_one_or_none()

            if existing:
                await session.execute(
                    update(WebPushSubscription)
                    .where(WebPushSubscription.endpoint_hash == endpoint_hash)
                    .values(
                        endpoint=endpoint,
                        p256dh=p256dh,
                        auth=auth,
                        user_agent=user_agent,
                        last_used_at=now,
                    )
                )
            else:
                session.add(
                    WebPushSubscription(
                        endpoint_hash=endpoint_hash,
                        endpoint=endpoint,
                        p256dh=p256dh,
                        auth=auth,
                        user_agent=user_agent,
                        created_at=now,
                        last_used_at=now,
                    )
                )
            await session.commit()

        logger.info("Web Push subscription registered: %s", endpoint_hash)
        return endpoint_hash

    async def unsubscribe(self, endpoint: str) -> bool:
        """Remove a push subscription. Returns True if deleted."""
        from app.database.connection import get_session
        from app.database.models import WebPushSubscription

        endpoint_hash = self._hash_endpoint(endpoint)

        async with get_session() as session:
            result = await session.execute(delete(WebPushSubscription).where(WebPushSubscription.endpoint_hash == endpoint_hash))
            await session.commit()

        deleted = bool(getattr(result, "rowcount", 0) > 0)
        if deleted:
            logger.info("Web Push subscription removed: %s", endpoint_hash)
        return deleted

    async def broadcast(self, title: str, body: str, url: str = "/") -> BroadcastResult:
        """Send a push notification to all registered subscriptions and report what reached them."""
        from app.database.connection import get_session
        from app.database.models import WebPushSubscription

        async with get_session() as session:
            result = await session.execute(select(WebPushSubscription))
            subscriptions = result.scalars().all()

        if not subscriptions:
            return BroadcastResult(subscriptions=0)

        payload = json.dumps(
            {"title": title, "body": body, "url": url},
            ensure_ascii=False,
        )

        tasks = [
            self._send_one(
                endpoint=sub.endpoint,
                p256dh=sub.p256dh,
                auth=sub.auth,
                payload=payload,
                endpoint_hash=sub.endpoint_hash,
            )
            for sub in subscriptions
        ]

        # Every send settles into None (delivered) or a reason, so gather never has an exception to hand back.
        send_reasons = await asyncio.gather(*tasks)
        broadcast_result = BroadcastResult(
            subscriptions=len(subscriptions),
            failures=tuple(reason for reason in send_reasons if reason is not None),
        )
        logger.info(
            "Web Push broadcast: %d/%d delivered (title=%s)",
            broadcast_result.delivered,
            broadcast_result.subscriptions,
            title[:50],
        )
        return broadcast_result

    async def _send_one(
        self,
        endpoint: str,
        p256dh: str,
        auth: str,
        payload: str,
        endpoint_hash: str,
    ) -> str | None:
        """Send to a single subscription. Auto-cleans gone subscriptions.

        Returns None once the push service accepted the push, otherwise the reason it did not (see ``_failure_reason``).
        """
        subscription_info: dict[str, Any] = {
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": auth},
        }

        vapid_claims = {
            "sub": f"mailto:{NOREPLY_EMAIL}",
        }

        from pywebpush import Vapid, WebPushException, webpush

        try:
            private_pem, _ = self._ensure_keys()
            await asyncio.to_thread(
                webpush,
                subscription_info=subscription_info,
                data=payload,
                # pywebpush takes a Vapid object, a key file path or a raw base64 key, but not PEM text.
                vapid_private_key=Vapid.from_pem(private_pem.encode()),
                vapid_claims=vapid_claims,
                timeout=_PUSH_REQUEST_TIMEOUT_SECONDS,
            )
            return None
        except WebPushException as exc:
            resp = getattr(exc, "response", None)
            if resp is not None:
                sc = getattr(resp, "status_code", None)
                if sc in _GONE_STATUS_CODES:
                    await self._remove_subscription(endpoint_hash)
                    logger.info(
                        "Removed expired subscription %s (HTTP %d)",
                        endpoint_hash,
                        sc,
                    )
                    return f"{_failure_reason(exc)} (subscription removed)"
            logger.warning("Web Push failed for %s: %s", endpoint_hash, exc)
            return _failure_reason(exc)
        except Exception as exc:
            logger.warning("Web Push failed for %s: %s", endpoint_hash, exc)
            return _failure_reason(exc)

    async def _remove_subscription(self, endpoint_hash: str) -> None:
        """Remove a subscription by its hash (internal cleanup)."""
        from app.database.connection import get_session
        from app.database.models import WebPushSubscription

        try:
            async with get_session() as session:
                await session.execute(delete(WebPushSubscription).where(WebPushSubscription.endpoint_hash == endpoint_hash))
                await session.commit()
        except Exception as exc:
            logger.warning("Failed to remove subscription %s: %s", endpoint_hash, exc)


_service: WebPushService | None = None


def get_web_push_service() -> WebPushService:
    """Singleton accessor — lazily created on first call."""
    global _service  # noqa: PLW0603
    if _service is None:
        _service = WebPushService()
    return _service
