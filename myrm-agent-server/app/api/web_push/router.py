"""Web Push REST API router.

[INPUT]
- app.core.web_push::get_web_push_service
- app.api.web_push.schemas

[OUTPUT]
- router: Web Push REST endpoints (VAPID key, subscribe, unsubscribe, test); the test reports the delivered count and why devices were missed

[POS]
API layer for Web Push. Thin delegation to WebPushService.
"""

import logging

from fastapi import APIRouter

from app.api.web_push.schemas import (
    WebPushSubscriptionRequest,
    WebPushSubscriptionResponse,
    WebPushTestRequest,
    WebPushTestResponse,
    WebPushUnsubscribeRequest,
    WebPushVapidKeyResponse,
)
from app.core.web_push.service import get_web_push_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web-push", tags=["Web Push"])

# A test with nobody registered reaches nobody, so it reports that as its reason rather than as a bare zero.
_NO_DEVICE_REASON = "No device is registered for Web Push on this server."


@router.get("/vapid-key", response_model=WebPushVapidKeyResponse)
async def get_vapid_public_key() -> WebPushVapidKeyResponse:
    """Return the VAPID application server public key for client-side subscription."""
    service = get_web_push_service()
    return WebPushVapidKeyResponse(public_key=service.public_key)


@router.post("/subscribe", response_model=WebPushSubscriptionResponse)
async def subscribe(req: WebPushSubscriptionRequest) -> WebPushSubscriptionResponse:
    """Register a new Web Push subscription."""
    service = get_web_push_service()
    endpoint_hash = await service.subscribe(
        endpoint=req.endpoint,
        p256dh=req.p256dh,
        auth=req.auth,
        user_agent=req.user_agent,
    )
    return WebPushSubscriptionResponse(endpoint_hash=endpoint_hash)


@router.post("/unsubscribe")
async def unsubscribe(req: WebPushUnsubscribeRequest) -> dict[str, str]:
    """Remove a Web Push subscription."""
    service = get_web_push_service()
    deleted = await service.unsubscribe(endpoint=req.endpoint)
    return {"status": "ok" if deleted else "not_found"}


@router.post("/test", response_model=WebPushTestResponse)
async def send_test(req: WebPushTestRequest) -> WebPushTestResponse:
    """Send a test push notification to all registered subscriptions and report why any did not arrive."""
    service = get_web_push_service()
    result = await service.broadcast(title=req.title, body=req.body, url=req.url)
    failures = list(result.reasons) if result.subscriptions else [_NO_DEVICE_REASON]
    return WebPushTestResponse(delivered=result.delivered, subscriptions=result.subscriptions, failures=failures)
