"""Tells the user, once, that Web Push is not getting through to any of their devices.

[INPUT]
- app.core.web_push.service::BroadcastResult (POS: what one broadcast reached, with a reason for each missed subscription)
- app.services.event.app_event_bus::AppEvent, AppEventType, ServerEventBus (POS: SSE / IM fan-out)

[OUTPUT]
- ALERT_COMPONENT: ``component`` of the HEALTH_ALERT, the value the WebUI recognises to show its own wording
- DeliveryAlert: observe() every broadcast; publishes one HEALTH_ALERT per streak of broadcasts that reached no device

[POS]
A failed push only ever lands in the log, and a browser that stops receiving pushes gives the user nothing to notice.
A streak starts with a broadcast that reached none of the registered devices and ends with the first broadcast that
reached one or found none registered: a lasting fault is one alert, a later fault is another. The alert carries
``suppress_web_push`` because Web Push is the channel that is failing; the WebUI and IM channels show it.
"""

from __future__ import annotations

from app.core.web_push.service import BroadcastResult
from app.services.event.app_event_bus import AppEvent, AppEventType, ServerEventBus

ALERT_COMPONENT = "web_push"


def _alert_event(result: BroadcastResult) -> AppEvent:
    reasons = "; ".join(result.reasons)
    return AppEvent(
        event_type=AppEventType.HEALTH_ALERT,
        data={
            "component": ALERT_COMPONENT,
            "status": "fail",
            "code": "all_deliveries_failed",
            "message": f"Browser push notifications could not be delivered to any registered device ({reasons}).",
            "detail": reasons,
            "fix_suggestion": "Open Settings > Preferences and send a test notification to see why.",
            "layer": "system",
            "suppress_web_push": True,
        },
    )


class DeliveryAlert:
    """Publishes the alert on the first broadcast of a streak that reached no registered device."""

    def __init__(self, bus: ServerEventBus) -> None:
        self._bus = bus
        # True while a streak is under way, i.e. its alert is already out.
        self._streak = False

    def observe(self, result: BroadcastResult) -> None:
        reached_none = result.subscriptions > 0 and result.delivered == 0
        if reached_none and not self._streak:
            self._bus.publish(_alert_event(result))
        self._streak = reached_none
