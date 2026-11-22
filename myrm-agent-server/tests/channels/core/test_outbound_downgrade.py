"""Tests for downgrade_components: components, quick replies and media fall back to text when a channel lacks them."""

from __future__ import annotations

import pytest

from app.channels.core.outbound_prepare import downgrade_components
from app.channels.types import (
    ActionButton,
    ChannelCapabilities,
    OutboundMessage,
    QuickReply,
    SelectMenu,
    SelectOption,
)
from tests.channels.bus_testkit import FakeChannel, make_out


class TestDowngradeComponents:
    def test_no_components_returns_same(self) -> None:
        ch = FakeChannel()
        msg = make_out()
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert result is msg

    def test_buttons_kept_when_supported(self) -> None:
        ch = FakeChannel(caps=ChannelCapabilities(buttons=True))
        btn = ActionButton(label="OK", action_id="test:ok")
        msg = make_out(components=((btn,),))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert result is msg

    def test_callback_buttons_are_dropped_when_unsupported(self) -> None:
        """A button with only a callback id has no text form: no handler resolves a command made from it."""
        ch = FakeChannel(caps=ChannelCapabilities(buttons=False))
        btn = ActionButton(label="OK", action_id="test:ok")
        msg = make_out(content="Choose:", components=((btn,),))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert result.components == ()
        assert result.content == "Choose:"

    def test_link_button_beside_a_callback_button_keeps_only_the_link(self) -> None:
        ch = FakeChannel(caps=ChannelCapabilities(buttons=False))
        approve = ActionButton(label="Approve", action_id="approval:approve:r1")
        open_link = ActionButton(label="Open", action_id="artifact:open", url="https://example.com/a")
        msg = make_out(content="Review:", components=((approve, open_link),))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert result.components == ()
        assert result.content == "Review:\n\n• Open → https://example.com/a"

    def test_select_menu_downgraded_independently(self) -> None:
        ch = FakeChannel(caps=ChannelCapabilities(buttons=True, select_menus=False))
        btn = ActionButton(label="OK", action_id="test:ok")
        sel = SelectMenu(
            action_id="test:sel",
            placeholder="Pick",
            options=(SelectOption(label="A", value="a"),),
        )
        msg = make_out(components=((btn,), (sel,)))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert len(result.components) == 1  # button row kept
        assert "A" in result.content or "Pick" in result.content

    def test_required_quick_replies_downgraded_when_unsupported(self) -> None:
        ch = FakeChannel(caps=ChannelCapabilities(quick_replies=False))
        qr = QuickReply(label="Yes", text="yes", required=True)
        msg = make_out(quick_replies=(qr,))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert len(result.quick_replies) == 0
        assert "Yes" in result.content

    def test_non_required_quick_replies_dropped_when_unsupported(self) -> None:
        ch = FakeChannel(caps=ChannelCapabilities(quick_replies=False))
        qr = QuickReply(label="Search", text="search")
        msg = make_out(quick_replies=(qr,))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert len(result.quick_replies) == 0
        assert "Search" not in result.content

    def test_mixed_quick_replies_only_required_downgraded(self) -> None:
        ch = FakeChannel(caps=ChannelCapabilities(quick_replies=False))
        suggestion = QuickReply(label="Search", text="search")
        action = QuickReply(label="Approve", text="/approve", required=True)
        msg = make_out(quick_replies=(suggestion, action))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert len(result.quick_replies) == 0
        assert "Approve" in result.content
        assert "Search" not in result.content

    def test_fallback_uses_english_by_default(self) -> None:
        """Test that fallback defaults to English for framework compliance."""
        ch = FakeChannel(caps=ChannelCapabilities(quick_replies=False))
        qr = QuickReply(label="Yes", text="yes", required=True)
        msg = make_out(quick_replies=(qr,))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert "Reply with a number" in result.content

    def test_fallback_uses_chinese_when_specified(self) -> None:
        """Test that fallback uses Chinese when explicitly requested."""
        ch = FakeChannel(caps=ChannelCapabilities(quick_replies=False))
        qr = QuickReply(label="Yes", text="yes", required=True)
        msg = OutboundMessage(
            channel="fake",
            recipient_id="user1",
            content="Do you agree?",
            user_id="test_user",
            quick_replies=(qr,),
            metadata={"locale": "zh"},
        )
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert "回复数字选择" in result.content
        assert "Reply with a number" not in result.content

    def test_downgrade_logs_info_message(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that downgrade events are logged at INFO level."""
        import logging

        caplog.set_level(logging.INFO)
        ch = FakeChannel(caps=ChannelCapabilities(buttons=False, quick_replies=False))
        button = ActionButton(label="Approve", action_id="approve")
        qr = QuickReply(label="Yes", text="yes", required=True)
        msg = OutboundMessage(
            channel="fake",
            recipient_id="user1",
            content="Choose:",
            user_id="test_user",
            components=((button,),),
            quick_replies=(qr,),
        )

        downgrade_components(msg, ch.capabilities, channel_name=ch.name)

        # Verify log was emitted
        assert len(caplog.records) == 1
        log_record = caplog.records[0]
        assert log_record.levelname == "INFO"
        assert "Downgrading components/media for channel 'fake'" in log_record.message
        assert "buttons" in log_record.message
        assert "quick_replies(1)" in log_record.message

    def test_url_button_downgrade_shows_link(self) -> None:
        """Test that URL buttons show the URL in fallback text."""
        ch = FakeChannel(caps=ChannelCapabilities(buttons=False))
        url_button = ActionButton(label="View Details", action_id="view", url="https://example.com/details")
        msg = make_out(components=((url_button,),))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert "View Details → https://example.com/details" in result.content
        assert "/view" not in result.content  # Should NOT show action_id for URL buttons

    def test_select_menu_without_placeholder_uses_default(self) -> None:
        """Test that SelectMenu without placeholder uses localized default text."""
        ch = FakeChannel(caps=ChannelCapabilities(select_menus=False))
        menu = SelectMenu(
            action_id="test:sel",
            placeholder="",  # Empty placeholder
            options=(
                SelectOption(label="Option A", value="a"),
                SelectOption(label="Option B", value="b"),
            ),
        )

        # English default
        msg_en = make_out(components=((menu,),))
        result_en = downgrade_components(msg_en, ch.capabilities, channel_name=ch.name)
        assert "Options:" in result_en.content or "• Options:" in result_en.content

        # Chinese
        msg_zh = OutboundMessage(
            channel="fake",
            recipient_id="user1",
            content="选择:",
            user_id="test_user",
            components=((menu,),),
            metadata={"locale": "zh"},
        )
        result_zh = downgrade_components(msg_zh, ch.capabilities, channel_name=ch.name)
        assert "选项:" in result_zh.content or "• 选项:" in result_zh.content

    def test_media_downgraded_when_unsupported(self) -> None:
        import dataclasses

        from app.channels.types import MediaAttachment, MediaType

        ch = FakeChannel(caps=ChannelCapabilities(media=False))
        media1 = MediaAttachment(media_type=MediaType.IMAGE, url="https://example.com/img.png")
        media2 = MediaAttachment(media_type=MediaType.VIDEO, path="/local/video.mp4")
        msg = make_out(content="Look at this:")
        msg = dataclasses.replace(msg, media=(media1, media2))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert len(result.media) == 0
        assert "[Image: https://example.com/img.png]" in result.content
        assert "Attachment not sent: video.mp4 (not supported in this chat)" in result.content

    def test_media_kept_when_supported(self) -> None:
        import dataclasses

        from app.channels.types import MediaAttachment, MediaType

        ch = FakeChannel(caps=ChannelCapabilities(media=True))
        media1 = MediaAttachment(media_type=MediaType.IMAGE, url="https://example.com/img.png")
        msg = make_out(content="Look at this:")
        msg = dataclasses.replace(msg, media=(media1,))
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert len(result.media) == 1
        assert "[Image: https://example.com/img.png]" not in result.content

    def test_mixed_components_and_media_downgraded_correctly(self) -> None:
        import dataclasses

        from app.channels.types import MediaAttachment, MediaType

        # Channel supports buttons but NOT media
        ch = FakeChannel(caps=ChannelCapabilities(buttons=True, media=False))
        btn = ActionButton(label="OK", action_id="test:ok")
        media1 = MediaAttachment(media_type=MediaType.IMAGE, url="https://example.com/img.png")

        msg = make_out(content="Choose:", components=((btn,),))
        msg = dataclasses.replace(msg, media=(media1,))

        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)

        # Buttons should be kept
        assert len(result.components) == 1
        kept = result.components[0][0]
        assert isinstance(kept, ActionButton)
        assert kept.label == "OK"

        # Media should be stripped and appended to text
        assert len(result.media) == 0
        assert "[Image: https://example.com/img.png]" in result.content

    def test_empty_media_list_ignored(self) -> None:
        import dataclasses

        ch = FakeChannel(caps=ChannelCapabilities(media=False))
        msg = make_out(content="Hello")
        msg = dataclasses.replace(msg, media=())
        result = downgrade_components(msg, ch.capabilities, channel_name=ch.name)
        assert result is msg
        assert result.content == "Hello"
