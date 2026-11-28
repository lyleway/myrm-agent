"""The memory credentials of a chat's external agents live and die with the chat's pool."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.external_agents import runtime_pool_registry
from app.services.external_agents.memory_grants import get_memory_grants, memory_server_for
from app.services.external_agents.runtime_pool_registry import ChatRuntimePoolRegistry, close_runtime_pool_bounded


@pytest.fixture(autouse=True)
def _short_close_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_pool_registry, "_POOL_CLOSE_TIMEOUT_SECONDS", 0.1)


async def _hang_forever() -> None:
    await asyncio.Event().wait()


def _pool(*, hangs_when_closing: bool = False) -> tuple[MagicMock, str]:
    """A pool whose agent was given the memory server, and the credential in it."""
    pool = MagicMock()
    pool.available_backends = ["gemini"]
    pool.close_all = AsyncMock(side_effect=_hang_forever if hangs_when_closing else None)
    pool.release_resident = AsyncMock()
    server = memory_server_for(pool, agent_id="agent-1", external_agent="gemini")
    return pool, server.headers["Authorization"].removeprefix("Bearer ")


def _builder(pool: MagicMock) -> Callable[[], Awaitable[MagicMock]]:
    async def build() -> MagicMock:
        return pool

    return build


def _reaches_memory(token: str) -> bool:
    return get_memory_grants().resolve(token) is not None


async def test_a_pool_that_is_closed_loses_its_credentials() -> None:
    pool, token = _pool()
    assert _reaches_memory(token)

    await close_runtime_pool_bounded(pool, scope="chat-1")

    assert not _reaches_memory(token)
    pool.close_all.assert_awaited_once()


async def test_a_pool_that_hangs_while_closing_has_already_lost_its_credentials() -> None:
    pool, token = _pool(hangs_when_closing=True)

    await close_runtime_pool_bounded(pool, scope="chat-1")

    assert not _reaches_memory(token)


async def test_closing_a_chat_ends_only_that_chats_credentials() -> None:
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)
    closing, closing_token = _pool()
    staying, staying_token = _pool()
    await registry.acquire("chat-closing", "fp", _builder(closing))
    await registry.acquire("chat-staying", "fp", _builder(staying))

    await registry.close_chat("chat-closing")

    assert not _reaches_memory(closing_token)
    assert _reaches_memory(staying_token)


async def test_a_pool_replaced_for_a_changed_config_loses_its_credentials() -> None:
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)
    old, old_token = _pool()
    new, new_token = _pool()
    await registry.acquire("chat-1", "fp-a", _builder(old))

    await registry.acquire("chat-1", "fp-b", _builder(new))

    assert not _reaches_memory(old_token)
    assert _reaches_memory(new_token)


async def test_closing_every_pool_ends_every_credential() -> None:
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)
    pools = [_pool() for _ in range(3)]
    for index, (pool, _) in enumerate(pools):
        await registry.acquire(f"chat-{index}", "fp", _builder(pool))

    await registry.close_all()

    assert [_reaches_memory(token) for _, token in pools] == [False, False, False]


async def test_a_pool_released_as_idle_keeps_its_credentials_for_the_next_turn() -> None:
    registry = ChatRuntimePoolRegistry(idle_seconds=0.01)
    idle, idle_token = _pool()
    await registry.acquire("chat-idle", "fp", _builder(idle))
    await asyncio.sleep(0.02)

    other, _ = _pool()
    await registry.acquire("chat-other", "fp", _builder(other))

    idle.release_resident.assert_awaited_once()
    idle.close_all.assert_not_awaited()
    assert _reaches_memory(idle_token)
