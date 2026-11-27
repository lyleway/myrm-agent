"""Unit tests for entitlement gap stream preflight."""

from __future__ import annotations

import time

import pytest

from app.services.agent.stream_session.entitlement_gap_preflight import (
    CapabilityGapEmissionTracker,
    build_web_search_config_gap_sse_event,
    reset_capability_gap_emission_tracker,
)


def setup_function() -> None:
    reset_capability_gap_emission_tracker()


def test_capability_gap_emission_tracker_re_emits_after_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = CapabilityGapEmissionTracker(cooldown_seconds=30.0)
    now = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)

    assert tracker.should_emit("chat-cooldown", "web_search:not_configured") is True
    tracker.mark_emitted("chat-cooldown", "web_search:not_configured")
    assert tracker.should_emit("chat-cooldown", "web_search:not_configured") is False

    now = 1031.0
    assert tracker.should_emit("chat-cooldown", "web_search:not_configured") is True


def test_build_web_search_config_gap_not_configured() -> None:
    event = build_web_search_config_gap_sse_event(
        message_id="msg-search-1",
        web_search_profile_enabled=True,
        enable_web_search=False,
        search_is_user_configured=False,
        chat_id="chat-search-1",
        locale="en",
    )
    assert event is not None
    data = event["data"]
    assert isinstance(data, dict)
    assert data["tool_id"] == "web_search"
    assert data["reason"] == "not_configured"
    assert data["settings_path"] == "/settings/search"


def test_build_web_search_config_gap_none_when_runtime_enabled() -> None:
    event = build_web_search_config_gap_sse_event(
        message_id="msg-search-2",
        web_search_profile_enabled=True,
        enable_web_search=True,
        search_is_user_configured=True,
        chat_id="chat-search-2",
        locale="en",
    )
    assert event is None


def test_resolve_web_search_config_gap_display_message_localized() -> None:
    from app.services.agent.stream_session.entitlement_gap_preflight import (
        resolve_web_search_config_gap_display_message,
    )

    zh = resolve_web_search_config_gap_display_message(reason="not_configured", locale="zh-CN")
    en = resolve_web_search_config_gap_display_message(reason="not_configured", locale="en")
    assert "未配置搜索 API" in zh
    assert "no search API is configured" in en
