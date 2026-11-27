"""DeliveryAlert and its place in WebPushDispatcher: one HEALTH_ALERT per streak of broadcasts that reach no device."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.notifications.adaptive_cards import format_legacy_text
from app.core.web_push.delivery_alert import ALERT_COMPONENT, DeliveryAlert
from app.core.web_push.dispatcher import WebPushDispatcher
from app.core.web_push.service import BroadcastResult
from app.services.event.app_event_bus import AppEvent, AppEventType, ServerEventBus

_REACHED_ALL = BroadcastResult(subscriptions=2)
_REACHED_ONE = BroadcastResult(subscriptions=2, failures=("HTTP 500",))
_REACHED_NONE = BroadcastResult(subscriptions=2, failures=("HTTP 403 Forbidden", "ConnectTimeout"))
_NOTHING_REGISTERED = BroadcastResult(subscriptions=0)


def _published(queue: asyncio.Queue[AppEvent]) -> list[AppEvent]:
    events: list[AppEvent] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.fixture
def bus() -> ServerEventBus:
    return ServerEventBus()


class TestAlertEvent:
    def test_every_registered_device_missed_raises_a_health_alert_with_the_reasons(self, bus: ServerEventBus) -> None:
        queue = bus.subscribe()

        DeliveryAlert(bus).observe(_REACHED_NONE)

        [event] = _published(queue)
        assert event.event_type == AppEventType.HEALTH_ALERT
        assert event.data["component"] == ALERT_COMPONENT == "web_push"
        assert event.data["status"] == "fail"
        assert event.data["detail"] == "HTTP 403 Forbidden; ConnectTimeout"
        assert "HTTP 403 Forbidden; ConnectTimeout" in event.data["message"]
        assert event.data["fix_suggestion"]
        # The alert exists because Web Push is not getting through, so it must not be sent through it.
        assert event.data["suppress_web_push"] is True

    def test_the_alert_reads_as_a_health_alert_in_im_channels(self, bus: ServerEventBus) -> None:
        queue = bus.subscribe()

        DeliveryAlert(bus).observe(_REACHED_NONE)

        [event] = _published(queue)
        text = format_legacy_text(event)
        assert text is not None
        assert text.startswith("[Myrm AI] Health alert (web_push): ")

    def test_equal_reasons_are_listed_once(self, bus: ServerEventBus) -> None:
        queue = bus.subscribe()

        DeliveryAlert(bus).observe(BroadcastResult(subscriptions=2, failures=("HTTP 410 Gone", "HTTP 410 Gone")))

        [event] = _published(queue)
        assert event.data["detail"] == "HTTP 410 Gone"


class TestStreaks:
    """When to alert, with the bus replaced by a recorder: the real bus drops an event equal to the one before it."""

    def test_a_broadcast_that_reaches_a_device_raises_nothing(self) -> None:
        bus = MagicMock()

        DeliveryAlert(bus).observe(_REACHED_ONE)

        bus.publish.assert_not_called()

    def test_no_registered_device_raises_nothing(self) -> None:
        bus = MagicMock()

        DeliveryAlert(bus).observe(_NOTHING_REGISTERED)

        bus.publish.assert_not_called()

    def test_a_streak_of_missed_broadcasts_raises_one_alert(self) -> None:
        bus = MagicMock()
        alert = DeliveryAlert(bus)

        for _ in range(3):
            alert.observe(_REACHED_NONE)

        assert bus.publish.call_count == 1

    @pytest.mark.parametrize("ending", [_REACHED_ALL, _REACHED_ONE, _NOTHING_REGISTERED])
    def test_the_next_fault_after_a_streak_ended_raises_another_alert(self, ending: BroadcastResult) -> None:
        bus = MagicMock()
        alert = DeliveryAlert(bus)

        alert.observe(_REACHED_NONE)
        alert.observe(ending)
        alert.observe(_REACHED_NONE)

        assert bus.publish.call_count == 2

    def test_an_alert_that_could_not_be_published_is_raised_again_by_the_next_broadcast(self) -> None:
        bus = MagicMock()
        bus.publish.side_effect = [RuntimeError("bus closed"), None]
        alert = DeliveryAlert(bus)

        with pytest.raises(RuntimeError):
            alert.observe(_REACHED_NONE)
        alert.observe(_REACHED_NONE)

        assert bus.publish.call_count == 2


class TestDispatcherRaisesTheAlert:
    @pytest.mark.asyncio
    async def test_failed_pushes_raise_one_alert_that_is_not_pushed_itself(self, bus: ServerEventBus) -> None:
        service = AsyncMock()
        service.broadcast = AsyncMock(return_value=_REACHED_NONE)
        dispatcher = WebPushDispatcher(bus)
        probe = bus.subscribe()

        with patch("app.core.web_push.service.get_web_push_service", return_value=service):
            await dispatcher.start()
            for title in ("first", "second"):
                bus.publish(AppEvent(event_type=AppEventType.SYSTEM_NOTIFICATION, data={"title": title, "message": "m"}))
            await asyncio.sleep(0.3)
            await dispatcher.stop()

        alerts = [event for event in _published(probe) if event.event_type == AppEventType.HEALTH_ALERT]
        assert len(alerts) == 1
        # Two notifications were pushed; the alert reached the dispatcher as a third event and was left alone.
        assert service.broadcast.await_count == 2
