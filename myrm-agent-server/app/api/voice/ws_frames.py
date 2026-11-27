"""JSON text frames the browser sends on the voice session WebSocket.

[INPUT]
- pydantic::BaseModel / ValidationError (POS: frame schema validation)

[OUTPUT]
- SessionFrame: the config frame and the control frames of /ws/voice/session as one schema with every field optional
- parse_session_frame: the frame a text message holds, or None when the message is unusable

[POS]
Edge validation for the voice session protocol. A text message that is not JSON, not an object or carries a wrongly
typed field is unusable as a whole, so handlers branch on None instead of probing untyped dict values.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError


class SessionFrame(BaseModel):
    """Every field the session protocol knows; a frame leaves the fields it does not use at their defaults."""

    type: str = ""
    text: str = ""
    mode: str = "audio_only"
    agent_id: str | None = None
    chat_id: str | None = None
    keyterms: list[str] = Field(default_factory=list)


def parse_session_frame(message: str) -> SessionFrame | None:
    """The frame `message` holds, or None when it is not valid JSON, not an object or a field has the wrong type."""
    try:
        return SessionFrame.model_validate_json(message)
    except ValidationError:
        return None
