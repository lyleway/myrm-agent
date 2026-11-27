"""The channel chat seed route leaves the chat an IM turn is stored in, and exists only in local mode.

[INPUT]
- app.api.chats.test_fixtures.channel_chat (POS: the seed route under test)
- app.services.chat.chat_service::ChatService (POS: the chat store the route writes to and the checks read from)

[OUTPUT]
Fails when the seeded chat is not the one the channel resolver finds under its session key, when the exchange is stored out
of order, when two seeds share a chat, when the chat is not bound to the agent the caller names (or the first agent when it
names none), or when the route writes anything outside local mode or without an agent to bind.

[POS]
Guards the fixture the hand-off Chrome E2E tests (tests/e2e/test_channel_web_handoff_landing_chrome_e2e.py and
test_channel_web_handoff_continue_chrome_e2e.py) seed their chat with.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.chat.chat_service import ChatService
from tests.support.minimal_app import build_minimal_app

_SEED_URL = "/api/v1/chats/test/seed-channel-chat-fixture"
_FIXTURE_MODULE = "app.api.chats.test_fixtures.channel_chat"
_AGENT_ID = "agent-e2e-channel-chat"


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """The chats API on the test database with the route reachable and one agent to bind, no agent row written."""
    agents = AsyncMock(return_value=([SimpleNamespace(id=_AGENT_ID)], 1))
    with (
        patch(f"{_FIXTURE_MODULE}.is_local_mode", return_value=True),
        patch(f"{_FIXTURE_MODULE}.AgentService.get_agent_list", new=agents),
    ):
        transport = ASGITransport(app=build_minimal_app(preset="chats"))
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def _seed(client: AsyncClient, *, agent_id: str | None = None) -> dict[str, str]:
    response = await client.post(_SEED_URL, params={"agent_id": agent_id} if agent_id else None)
    assert response.status_code == 200, response.text
    body: dict[str, str] = response.json()
    return body


async def _channel_chat_total() -> int:
    _chats, total = await ChatService.get_chat_list(1, 1, source="telegram")
    return total


async def test_the_seed_is_the_chat_a_channel_turn_resolves_under_its_session_key(client: AsyncClient) -> None:
    body = await _seed(client)

    chat = await ChatService.get_chat_by_id(body["chat_id"])
    assert chat is not None
    assert (chat.source, body["source"]) == ("telegram", "telegram")
    assert chat.title == body["user_text"][:50], "the title is cut from the first user text, as after any first turn"
    assert (chat.agent_id, body["agent_id"]) == (_AGENT_ID, _AGENT_ID)
    assert body["ui_path"] == f"/{chat.id}"
    session_key = chat.channel_session_key
    assert session_key and session_key.startswith("telegram:dm:e2e-")

    # The next turn of that conversation lands in this very chat, which is the chat a hand-off link opens.
    resolved = await ChatService.get_or_create_channel_chat(session_key, "telegram", agent_id=_AGENT_ID)
    assert resolved.id == chat.id


async def test_the_user_text_is_stored_before_the_assistant_reply(client: AsyncClient) -> None:
    body = await _seed(client)

    response = await client.get(f"/api/v1/chats/{body['chat_id']}/messages")

    assert response.status_code == 200, response.text
    messages = response.json()["data"]["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", body["user_text"]),
        ("assistant", body["assistant_text"]),
    ]


async def test_a_named_agent_is_the_agent_the_chat_is_bound_to(client: AsyncClient) -> None:
    agents = AsyncMock(return_value=([SimpleNamespace(id=_AGENT_ID)], 1))

    with patch(f"{_FIXTURE_MODULE}.AgentService.get_agent_list", new=agents):
        body = await _seed(client, agent_id="agent-named")

    chat = await ChatService.get_chat_by_id(body["chat_id"])
    assert chat is not None
    assert (chat.agent_id, body["agent_id"]) == ("agent-named", "agent-named")
    agents.assert_not_awaited()


async def test_every_seed_is_a_chat_of_its_own(client: AsyncClient) -> None:
    before = await _channel_chat_total()

    first, second = await _seed(client), await _seed(client)

    assert first["chat_id"] != second["chat_id"]
    assert await _channel_chat_total() == before + 2


async def test_outside_local_mode_the_route_does_not_exist_and_stores_nothing(client: AsyncClient) -> None:
    before = await _channel_chat_total()

    with patch(f"{_FIXTURE_MODULE}.is_local_mode", return_value=False):
        response = await client.post(_SEED_URL)

    assert response.status_code == 404
    assert await _channel_chat_total() == before


async def test_without_an_agent_the_route_says_so_and_stores_nothing(client: AsyncClient) -> None:
    before = await _channel_chat_total()

    with patch(f"{_FIXTURE_MODULE}.AgentService.get_agent_list", new=AsyncMock(return_value=([], 0))):
        response = await client.post(_SEED_URL)

    assert response.status_code == 500
    assert "No agents available" in response.text
    assert await _channel_chat_total() == before
