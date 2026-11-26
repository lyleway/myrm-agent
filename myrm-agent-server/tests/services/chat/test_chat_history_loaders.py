"""History loaders: the compaction summary, the reasoning of assistant turns, and what is left out.

Both loaders read a real SQLite database. The system-role filter is covered in ``test_system_prompt_filtering.py``.
"""

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Chat, Message
from app.services.chat.chat_service import ChatService

_SUMMARY = "Docker Compose was chosen."
_SUMMARY_ENTRY = f"[Previous conversation summary]\n{_SUMMARY}"
_START = datetime(2026, 4, 17, 12, 0, tzinfo=UTC)


class _Turn(NamedTuple):
    role: str
    content: str
    extra_data: dict[str, object] | None = None


def _utc(value: datetime) -> datetime:
    """SQLite hands back times without an offset; they are UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _seed_chat(db: AsyncSession, *turns: _Turn, summarized_turns: int = 0, summarized_at: datetime | None = None) -> str:
    """Insert a chat whose turns were created a minute apart; the summary stands in for the first ``summarized_turns``."""
    chat_id = "chat-history"
    message_ids = [f"{chat_id}-m{number}" for number in range(1, len(turns) + 1)]
    chat = Chat(id=chat_id, title="History", action_mode="agent")
    if summarized_turns:
        chat.compacted_summary = _SUMMARY
        chat.compacted_before_id = message_ids[summarized_turns - 1]
        chat.compacted_at = summarized_at
    db.add(chat)
    for number, (message_id, turn) in enumerate(zip(message_ids, turns, strict=True), start=1):
        created = _START + timedelta(minutes=number)
        db.add(
            Message(
                id=message_id,
                chat_id=chat_id,
                role=turn.role,
                content=turn.content,
                extra_data=turn.extra_data,
                sent_at=created,
                sent_timezone="UTC",
                created_at=created,
            )
        )
    await db.commit()
    return chat_id


_TWO_ROUNDS = (
    _Turn("user", "Which tool?"),
    _Turn("assistant", "Docker Compose."),
    _Turn("user", "And for production?"),
    _Turn("assistant", "Kubernetes."),
)


@pytest.mark.asyncio
async def test_web_history_opens_with_the_summary_and_skips_the_turns_it_stands_for(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, *_TWO_ROUNDS, summarized_turns=2)

    history = await ChatService.load_web_chat_history(chat_id)

    assert [(entry[0], entry[1]) for entry in history] == [
        ("assistant", _SUMMARY_ENTRY),
        ("human", "And for production?"),
        ("assistant", "Kubernetes."),
    ]


@pytest.mark.asyncio
async def test_web_history_leaves_out_the_excluded_message_and_keeps_the_latest_ones(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, *_TWO_ROUNDS)

    history = await ChatService.load_web_chat_history(chat_id, exclude_message_id=f"{chat_id}-m4", max_messages=2)

    assert [entry[1] for entry in history] == ["Docker Compose.", "And for production?"]


@pytest.mark.asyncio
async def test_web_history_replays_the_content_blocks_of_a_turn_sent_with_attachments(db_session: AsyncSession) -> None:
    blocks = [{"type": "text", "text": "Which tool?"}, {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}]
    chat_id = await _seed_chat(db_session, _Turn("user", "Which tool?", {"original_query": blocks}))

    history = await ChatService.load_web_chat_history(chat_id)

    assert history[0][:2] == ["human", blocks]


@pytest.mark.parametrize(
    ("extra_data", "reasoning_meta"),
    [
        ({"reasoning": "Compose needs no cluster."}, {"reasoning_content": "Compose needs no cluster."}),
        ({"reasoning": ""}, None),
        ({"reasoning": 3}, None),
        ({"model": "any"}, None),
        (None, None),
    ],
)
@pytest.mark.asyncio
async def test_web_history_hands_an_assistant_turn_its_reasoning_only_when_there_is_text(
    db_session: AsyncSession, extra_data: dict[str, object] | None, reasoning_meta: dict[str, str] | None
) -> None:
    chat_id = await _seed_chat(db_session, _Turn("user", "Which tool?"), _Turn("assistant", "Compose.", extra_data))

    history = await ChatService.load_web_chat_history(chat_id)

    assert history[1] == (["assistant", "Compose."] if reasoning_meta is None else ["assistant", "Compose.", reasoning_meta])


@pytest.mark.asyncio
async def test_channel_history_opens_with_the_summary_stamped_when_it_was_made(db_session: AsyncSession) -> None:
    made_at = _START + timedelta(hours=1)
    chat_id = await _seed_chat(
        db_session, *_TWO_ROUNDS, _Turn("user", "And the rollout?"), summarized_turns=2, summarized_at=made_at
    )

    history = await ChatService.load_channel_history(chat_id)

    # The newest message is the one being answered, so the history stops before it.
    assert [(entry.role, entry.content) for entry in history] == [
        ("assistant", _SUMMARY_ENTRY),
        ("human", "And for production?"),
        ("assistant", "Kubernetes."),
    ]
    assert _utc(history[0].created_at) == made_at


@pytest.mark.asyncio
async def test_channel_history_stamps_a_summary_without_a_stored_time_with_now(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, *_TWO_ROUNDS, summarized_turns=2)

    before = datetime.now(UTC)
    history = await ChatService.load_channel_history(chat_id)

    assert before <= _utc(history[0].created_at) <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_channel_history_is_the_summary_alone_when_it_covers_every_message(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, *_TWO_ROUNDS[:2], summarized_turns=2)

    history = await ChatService.load_channel_history(chat_id)

    assert [(entry.role, entry.content) for entry in history] == [("assistant", _SUMMARY_ENTRY)]


@pytest.mark.asyncio
async def test_channel_history_of_a_chat_without_messages_is_empty(db_session: AsyncSession) -> None:
    assert await ChatService.load_channel_history("no-such-chat") == []
