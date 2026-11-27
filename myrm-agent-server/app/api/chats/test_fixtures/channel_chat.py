"""Local-only IM-origin chat Chrome E2E seed route.

[INPUT]
app.config.deploy_mode::is_local_mode (POS: local/tauri gate)
app.services.agent.agent_service::AgentService (POS: agent list, first agent bound to the seed chat when the caller names none)
app.services.chat.chat_service::ChatService (POS: channel chat resolution and message persistence, as an IM turn uses them)

[OUTPUT]
seed_channel_chat_fixture: the chat an IM turn is stored in (channel session key, titled from the user text) with one exchange,
    bound to the agent the caller names or else the first agent

[POS]
Mounted via test_fixtures/__init__.py router include.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.config.deploy_mode import is_local_mode
from app.services.agent.agent_service import AgentService
from app.services.chat.chat_service import ChatService

router = APIRouter()

_CHANNEL = "telegram"
_USER_TEXT = "IM hand-off Chrome E2E question asked in a chat app"
_ASSISTANT_TEXT = "IM hand-off Chrome E2E answer that the chat app link opens here in the browser."


async def _agent_id_to_bind(requested: str | None) -> str:
    """The agent the IM conversation ran on: the one the caller names, or else the first agent."""
    if requested:
        return requested
    agents, _total = await AgentService.get_agent_list(1, 100)
    if not agents:
        raise HTTPException(status_code=500, detail="No agents available for channel chat E2E seed")
    return str(agents[0].id)


@router.post("/test/seed-channel-chat-fixture", include_in_schema=False)
async def seed_channel_chat_fixture(agent_id: str | None = None) -> dict[str, str]:
    """Local dev/test only: seed the chat of an IM conversation, as the channel executor stores one."""
    if not is_local_mode():
        raise HTTPException(status_code=404, detail="Not found")

    bound_agent_id = await _agent_id_to_bind(agent_id)
    chat = await ChatService.get_or_create_channel_chat(
        f"{_CHANNEL}:dm:e2e-{uuid4().hex[:8]}",
        _CHANNEL,
        agent_id=bound_agent_id,
    )

    now = datetime.now(UTC)
    await ChatService.append_message(chat.id, "user", _USER_TEXT, now, "UTC")
    await ChatService.append_message(chat.id, "assistant", _ASSISTANT_TEXT, now, "UTC", message_id=str(uuid4()))

    return {
        "chat_id": chat.id,
        "agent_id": bound_agent_id,
        "source": _CHANNEL,
        "user_text": _USER_TEXT,
        "assistant_text": _ASSISTANT_TEXT,
        "ui_path": f"/{chat.id}",
    }
