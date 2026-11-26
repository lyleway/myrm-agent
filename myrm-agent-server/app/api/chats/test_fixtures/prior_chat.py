"""Local-only prior_chat Chrome E2E seed routes.

[INPUT]
- app.config.deploy_mode::is_local_mode (POS: gate local-only access)
- app.services.chat.chat_service::ChatService (POS: seed chat + messages)
- app.database.repositories.conversation_recall::ConversationRecallRepository (POS: rebuild recall index)

[OUTPUT]
- router: POST /test/seed-prior-chat-fixture (POS: E2E seed for @chat mention picker and search-result time checks; optional sent_at pins the message time, optional marker makes the seed searchable by itself)

[POS]
app.api.chats — Chrome E2E fixture for composer @chat: prior conversation mention.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.config.deploy_mode import is_local_mode
from app.database.connection import get_session
from app.database.dto import ChatCreate
from app.database.repositories.conversation_recall import ConversationRecallRepository
from app.services.agent.agent_service import AgentService
from app.services.chat.chat_service import ChatService

router = APIRouter()

_PRIOR_TITLE = "Prior chat E2E Alpha project"
_PRIOR_USER = "We decided the Alpha project should use Redis for caching."
_PRIOR_ASSISTANT = "Agreed — Redis fits the Alpha latency requirements."


@router.post("/test/seed-prior-chat-fixture", include_in_schema=False)
async def seed_prior_chat_fixture(
    sent_at: datetime | None = None,
    marker: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9]{1,40}$")] = None,
) -> dict[str, str]:
    """Local dev/test only: seed prior + composer chats indexed for @chat picker.

    ``sent_at`` stamps the prior chat's messages (default: now), so a check can pin the instant that time-window and
    date-display assertions depend on; it must carry a UTC offset. ``marker`` (one alphanumeric token) is appended to
    both messages, so a check can search for exactly this seed however many earlier seeds the database holds.
    """
    if not is_local_mode():
        raise HTTPException(status_code=404, detail="Not found")
    if sent_at is not None and sent_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="sent_at must include a UTC offset")

    agents, _total = await AgentService.get_agent_list(1, 100)
    if not agents:
        raise HTTPException(status_code=500, detail="No agents available for prior_chat E2E seed")

    agent = agents[0]
    prior_chat_id = f"e2eprior{uuid4().hex[:8]}"
    composer_chat_id = f"e2ecomp{uuid4().hex[:8]}"
    message_time = sent_at or datetime.now(UTC)
    tag = f" Ref {marker}." if marker else ""
    timezone = "UTC"

    await ChatService.create_or_update_chat(
        ChatCreate(
            chat_id=prior_chat_id,
            title=_PRIOR_TITLE,
            agent_id=agent.id,
            messages=[],
        ),
    )
    await ChatService.append_message(prior_chat_id, "user", _PRIOR_USER + tag, message_time, timezone)
    await ChatService.append_message(
        prior_chat_id,
        "assistant",
        _PRIOR_ASSISTANT + tag,
        message_time,
        timezone,
    )

    await ChatService.create_or_update_chat(
        ChatCreate(
            chat_id=composer_chat_id,
            title="Prior chat composer E2E",
            agent_id=agent.id,
            messages=[],
        ),
    )

    async with get_session() as db:
        await ConversationRecallRepository.rebuild_chat(db, prior_chat_id)
        await db.commit()

    return {
        "prior_chat_id": prior_chat_id,
        "prior_chat_title": _PRIOR_TITLE,
        "composer_chat_id": composer_chat_id,
        "ui_path": f"/{composer_chat_id}",
    }
