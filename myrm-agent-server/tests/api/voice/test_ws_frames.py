"""Voice session frames: which text messages from the browser are usable as the config or a control message."""

from __future__ import annotations

import json

import pytest

from app.api.voice.ws_frames import SessionFrame, parse_session_frame


class TestParseSessionFrame:
    def test_the_config_the_web_app_sends_keeps_all_its_fields(self) -> None:
        message = json.dumps(
            {"type": "config", "mode": "agent_bridge", "agent_id": "a1", "chat_id": "chat-1", "keyterms": ["Myrm"]}
        )

        assert parse_session_frame(message) == SessionFrame(
            type="config", mode="agent_bridge", agent_id="a1", chat_id="chat-1", keyterms=["Myrm"]
        )

    def test_a_frame_leaves_the_fields_it_does_not_use_at_their_defaults(self) -> None:
        assert parse_session_frame("{}") == SessionFrame(
            type="", text="", mode="audio_only", agent_id=None, chat_id=None, keyterms=[]
        )

    def test_the_ids_the_web_app_sends_for_a_new_chat_as_null_are_none(self) -> None:
        frame = parse_session_frame('{"type": "config", "agent_id": null, "chat_id": null}')

        assert frame is not None
        assert (frame.agent_id, frame.chat_id) == (None, None)

    def test_fields_the_protocol_does_not_know_are_ignored(self) -> None:
        assert parse_session_frame('{"type": "tts", "text": "hi", "volume": 11}') == SessionFrame(type="tts", text="hi")

    @pytest.mark.parametrize(
        "message",
        [
            "",
            "not json",
            "[]",
            "null",
            "3",
            '"config"',
            '{"type": ["config"]}',
            '{"text": 5}',
            '{"mode": 1}',
            '{"agent_id": 7}',
            '{"chat_id": []}',
            '{"keyterms": "Myrm"}',
            '{"keyterms": ["Myrm", 3]}',
        ],
        ids=[
            "empty",
            "not json",
            "an array",
            "null",
            "a number",
            "a string",
            "type not a string",
            "text not a string",
            "mode not a string",
            "agent id not a string",
            "chat id not a string",
            "keyterms not a list",
            "keyterms not all strings",
        ],
    )
    def test_a_message_that_is_not_a_well_typed_object_is_unusable(self, message: str) -> None:
        assert parse_session_frame(message) is None
