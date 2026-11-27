"""Inbound parsing: Pydantic update models and the messages they carry (text, media, contacts)."""

from __future__ import annotations

import pytest

from app.channels.providers.telegram.models import (
    TgMessage,
    TgUpdate,
)
from app.channels.types import MediaType
from tests.channels.providers.telegram.telegram_channel_factories import make_channel

_ADA_VCARD = "\n".join(
    ["BEGIN:VCARD", "VERSION:3.0", "FN:Ada Lovelace", "TEL;TYPE=CELL:+441234", "EMAIL:ada@example.com", "END:VCARD"]
)


def _private_update(sender: dict[str, object] | None = None, **message: object) -> dict[str, object]:
    """A private-chat update from user 42 whose message carries ``message`` fields."""
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": 42, **(sender or {})},
            "chat": {"id": 42, "type": "private"},
            **message,
        },
    }


class TestTgModels:
    """Pydantic model validation tests."""

    def test_update_from_dict(self) -> None:
        raw = {
            "update_id": 100,
            "message": {
                "message_id": 1,
                "from": {"id": 42, "username": "alice"},
                "chat": {"id": -100, "type": "private"},
                "text": "hello",
            },
        }
        tg = TgUpdate.model_validate(raw)
        assert tg.update_id == 100
        assert tg.message is not None
        assert tg.message.from_user is not None
        assert tg.message.from_user.id == 42
        assert tg.message.from_user.username == "alice"
        assert tg.message.chat is not None
        assert tg.message.chat.id == -100

    def test_update_callback_query(self) -> None:
        raw = {
            "update_id": 200,
            "callback_query": {
                "id": "cbq_1",
                "from": {"id": 42},
                "data": "qr:hello",
                "message": {"message_id": 5, "chat": {"id": -100, "type": "group"}},
            },
        }
        tg = TgUpdate.model_validate(raw)
        assert tg.callback_query is not None
        assert tg.callback_query.id == "cbq_1"
        assert tg.callback_query.data == "qr:hello"

    def test_update_extra_fields_allowed(self) -> None:
        raw = {"update_id": 1, "unknown_field": "value"}
        tg = TgUpdate.model_validate(raw)
        assert tg.update_id == 1

    def test_message_with_media(self) -> None:
        raw = {
            "message_id": 10,
            "from": {"id": 42},
            "chat": {"id": -100, "type": "private"},
            "photo": [{"file_id": "small"}, {"file_id": "large"}],
            "caption": "a photo",
        }
        msg = TgMessage.model_validate(raw)
        assert msg.photo is not None
        assert len(msg.photo) == 2
        assert msg.photo[-1].file_id == "large"
        assert msg.caption == "a photo"

    def test_message_with_sticker(self) -> None:
        raw = {
            "message_id": 11,
            "from": {"id": 42},
            "chat": {"id": -100, "type": "private"},
            "sticker": {"file_id": "stk_1", "emoji": "\U0001f60a"},
        }
        msg = TgMessage.model_validate(raw)
        assert msg.sticker is not None
        assert msg.sticker.emoji == "\U0001f60a"

    def test_message_with_location(self) -> None:
        raw = {
            "message_id": 12,
            "from": {"id": 42},
            "chat": {"id": -100, "type": "private"},
            "location": {"latitude": 39.9042, "longitude": 116.4074},
        }
        msg = TgMessage.model_validate(raw)
        assert msg.location is not None
        assert msg.location.latitude == pytest.approx(39.9042)
        assert msg.location.longitude == pytest.approx(116.4074)

    def test_message_with_venue(self) -> None:
        raw = {
            "message_id": 13,
            "from": {"id": 42},
            "chat": {"id": -100, "type": "private"},
            "venue": {
                "location": {"latitude": 40.35, "longitude": 116.00},
                "title": "Great Wall",
                "address": "Badaling",
                "foursquare_id": "4bf58dd8d48988d1e0931735",
            },
        }
        msg = TgMessage.model_validate(raw)
        assert msg.venue is not None
        assert msg.venue.title == "Great Wall"
        assert msg.venue.address == "Badaling"
        assert msg.venue.foursquare_id == "4bf58dd8d48988d1e0931735"
        assert msg.venue.location.latitude == pytest.approx(40.35)


class TestParseUpdate:
    """Test TelegramInboundMixin._parse_update with Pydantic models."""

    def test_text_message(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 42, "username": "alice", "first_name": "Alice"},
                "chat": {"id": 42, "type": "private"},
                "text": "hello world",
            },
        }
        msg = ch._parse_update(raw)
        assert msg is not None
        assert msg.content == "hello world"
        assert msg.sender_id == "42"
        assert msg.chat_id == "42"
        assert msg.is_group is False

    def test_empty_text_no_media_returns_none(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "text": "   ",
            },
        }
        assert ch._parse_update(raw) is None

    def test_no_message_returns_none(self) -> None:
        ch = make_channel()
        assert ch._parse_update({"update_id": 3}) is None

    def test_no_from_returns_none(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 4,
            "message": {
                "message_id": 12,
                "chat": {"id": 42, "type": "private"},
                "text": "hello",
            },
        }
        assert ch._parse_update(raw) is None

    def test_message_model_without_sender_is_not_converted(self) -> None:
        ch = make_channel()
        msg = TgMessage.model_validate({"message_id": 12, "chat": {"id": 42, "type": "private"}, "text": "hello"})
        assert ch._parse_message_model(msg, is_edit=False) is None

    def test_edited_message(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 5,
            "edited_message": {
                "message_id": 13,
                "from": {"id": 42, "username": "alice"},
                "chat": {"id": 42, "type": "private"},
                "text": "edited text",
            },
        }
        msg = ch._parse_update(raw)
        assert msg is not None
        assert msg.content == "edited text"
        assert msg.metadata.get("is_edit") is True

    def test_photo_message(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 6,
            "message": {
                "message_id": 14,
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "photo": [{"file_id": "small"}, {"file_id": "large"}],
                "caption": "a photo",
            },
        }
        msg = ch._parse_update(raw)
        assert msg is not None
        assert msg.content == "a photo"
        assert len(msg.media) == 1
        assert msg.media[0].media_type == MediaType.IMAGE
        assert msg.metadata.get("photo_file_id") == "large"

    def test_document_message(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 7,
            "message": {
                "message_id": 15,
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "document": {
                    "file_id": "doc_1",
                    "file_name": "report.pdf",
                    "mime_type": "application/pdf",
                },
            },
        }
        msg = ch._parse_update(raw)
        assert msg is not None
        assert len(msg.media) == 1
        assert msg.media[0].media_type == MediaType.DOCUMENT
        assert msg.media[0].filename == "report.pdf"
        assert msg.media[0].mime_type == "application/pdf"

    def test_voice_message(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 8,
            "message": {
                "message_id": 16,
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "voice": {"file_id": "voice_1", "duration": 5, "mime_type": "audio/ogg"},
            },
        }
        msg = ch._parse_update(raw)
        assert msg is not None
        assert len(msg.media) == 1
        assert msg.media[0].media_type == MediaType.AUDIO
        assert msg.metadata.get("voice_is_voice_note") is True
        assert msg.metadata.get("voice_duration") == 5

    def test_video_message(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 9,
            "message": {
                "message_id": 17,
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "video": {"file_id": "vid_1"},
                "caption": "a video",
            },
        }
        msg = ch._parse_update(raw)
        assert msg is not None
        assert len(msg.media) == 1
        assert msg.media[0].media_type == MediaType.VIDEO

    def test_sticker_message(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 10,
            "message": {
                "message_id": 18,
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "sticker": {"file_id": "stk_1", "emoji": "\U0001f60a"},
            },
        }
        msg = ch._parse_update(raw)
        assert msg is not None
        assert msg.content == "\U0001f60a"
        assert msg.metadata.get("is_sticker") is True

    def test_thread_id_parsed(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 14,
            "message": {
                "message_id": 22,
                "from": {"id": 42},
                "chat": {"id": -100, "type": "supergroup", "is_forum": True},
                "text": "forum message",
                "message_thread_id": 999,
            },
        }
        msg = ch._parse_update(raw)
        assert msg is not None
        assert msg.thread_id == "999"
        assert msg.metadata.get("is_forum") is True

    def test_invalid_payload_returns_none(self) -> None:
        """A payload the update model rejects is skipped instead of raised."""
        ch = make_channel()
        assert ch._parse_update({"update_id": "not a number"}) is None

    def test_audio_message(self) -> None:
        ch = make_channel()
        raw = {
            "update_id": 18,
            "message": {
                "message_id": 30,
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "audio": {"file_id": "audio_1", "duration": 120, "mime_type": "audio/mp3"},
            },
        }
        msg = ch._parse_update(raw)
        assert msg is not None
        assert len(msg.media) == 1
        assert msg.media[0].media_type == MediaType.AUDIO
        assert msg.metadata.get("voice_is_voice_note") is False

    def test_shared_contact_becomes_text_and_a_contact_card(self) -> None:
        ch = make_channel()
        contact = {"phone_number": "+441234", "first_name": "Ada", "last_name": "Lovelace"}
        msg = ch._parse_update(_private_update(contact=contact))
        assert msg is not None
        assert msg.content == "[Shared Contact] Name: Ada Lovelace | Phone: +441234"
        assert msg.metadata["contact_cards"] == [{"name": "Ada Lovelace", "phones": ["+441234"]}]

    def test_shared_contact_with_a_vcard_carries_the_vcard_fields(self) -> None:
        ch = make_channel()
        contact = {"phone_number": "+441234", "first_name": "Ada", "vcard": _ADA_VCARD}
        msg = ch._parse_update(_private_update(contact=contact))
        assert msg is not None
        assert msg.metadata["contact_cards"] == [{"name": "Ada Lovelace", "phones": ["+441234"], "emails": ["ada@example.com"]}]

    def test_shared_contact_with_an_empty_vcard_keeps_the_contact_fields(self) -> None:
        ch = make_channel()
        contact = {"phone_number": "+441234", "first_name": "Ada", "last_name": "Lovelace", "vcard": "no fields"}
        msg = ch._parse_update(_private_update(contact=contact))
        assert msg is not None
        assert msg.metadata["contact_cards"] == [{"name": "Ada Lovelace", "phones": ["+441234"]}]

    def test_video_note_is_a_video_attachment(self) -> None:
        ch = make_channel()
        msg = ch._parse_update(_private_update(video_note={"file_id": "vn_1", "duration": 5}))
        assert msg is not None
        assert [m.media_type for m in msg.media] == [MediaType.VIDEO]
        assert msg.metadata["video_file_id"] == "vn_1"
        assert msg.metadata["video_is_video_note"] is True

    def test_sender_language_code_is_recorded(self) -> None:
        ch = make_channel()
        msg = ch._parse_update(_private_update({"language_code": "de"}, text="hallo"))
        assert msg is not None
        assert msg.metadata["language_code"] == "de"
