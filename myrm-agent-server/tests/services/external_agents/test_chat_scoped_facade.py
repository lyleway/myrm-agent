"""The chat-scoped facade of a RuntimePool: turns, cancel, re-adoption of a closed pool, idle release."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.external_agents.runtime_pool_registry import (
    ChatRuntimePoolRegistry,
    ChatScopedRuntimePoolFacade,
)

_WORKSPACE = "/work/repo"


async def _build_mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.available_backends = ["claude"]
    pool.close_all = AsyncMock()
    pool.release_resident = AsyncMock()
    return pool


@pytest.mark.asyncio
async def test_facade_cancel_does_not_deadlock_during_run_turn() -> None:
    registry = ChatRuntimePoolRegistry()
    entered = asyncio.Event()
    unblock = asyncio.Event()

    async def slow_run_turn(
        name: str,
        prompt: str,
        session_id: str,
        *,
        workspace: str,
        mode: str = "persistent",
        mcp_servers: list | None = None,
        images: list[str] | None = None,
    ):
        entered.set()
        await unblock.wait()
        yield MagicMock()

    pool = MagicMock()
    pool.run_turn = slow_run_turn
    pool.cancel = AsyncMock()

    async def build_pool() -> MagicMock:
        return pool

    await registry.acquire("chat-1", "fp-a", build_pool)
    facade = ChatScopedRuntimePoolFacade(pool, "chat-1", registry, "fp-a")

    async def consume() -> None:
        async for _ in facade.run_turn("claude", "hi", "s1", workspace=_WORKSPACE):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    await asyncio.wait_for(facade.cancel("claude", "s1"), timeout=1.0)
    pool.cancel.assert_awaited_once_with("claude", "s1")
    unblock.set()
    await task


@pytest.mark.asyncio
async def test_a_facade_turn_stays_in_flight_until_the_pool_turn_has_stopped() -> None:
    """Closing the facade's turn closes the pool's turn first: the chat counts as busy until the process stopped."""
    registry = ChatRuntimePoolRegistry()
    in_flight_while_stopping: list[bool] = []

    async def run_turn(
        name: str,
        prompt: str,
        session_id: str,
        *,
        workspace: str,
        mode: str = "persistent",
        mcp_servers: list | None = None,
        images: list[str] | None = None,
    ):
        try:
            while True:
                yield MagicMock()
        finally:
            await asyncio.sleep(0.01)  # stopping the external process takes a moment
            in_flight_while_stopping.append(registry._is_turn_active("chat-1"))

    pool = MagicMock()
    pool.run_turn = run_turn

    async def build_pool() -> MagicMock:
        return pool

    await registry.acquire("chat-1", "fp-a", build_pool)
    facade = ChatScopedRuntimePoolFacade(pool, "chat-1", registry, "fp-a")

    turn = facade.run_turn("claude", "hi", "s1", workspace=_WORKSPACE)
    await anext(turn)
    assert registry._is_turn_active("chat-1")

    await turn.aclose()

    assert in_flight_while_stopping == [True]
    assert not registry._is_turn_active("chat-1")


@pytest.mark.asyncio
async def test_facade_exposes_only_the_delegation_surface() -> None:
    registry = ChatRuntimePoolRegistry()
    pool = MagicMock()
    pool.available_backends = ["claude", "codex"]
    pool.get_config = MagicMock(return_value={"name": "claude"})

    facade = ChatScopedRuntimePoolFacade(pool, "chat-1", registry, "fp-a")
    assert facade.available_backends == ["claude", "codex"]
    assert facade.get_config("claude") == {"name": "claude"}
    # Anything beyond the delegation surface would run turns the registry does not see.
    assert not hasattr(facade, "prompt")


def _build_turn_pool(label: str, ran_on: list[str]) -> MagicMock:
    """A pool whose run_turn records which pool served the turn."""

    async def run_turn(
        name: str, prompt: str, session_id: str, *, workspace: str, mode: str = "persistent", mcp_servers=None, images=None
    ):
        ran_on.append(label)
        yield MagicMock()

    pool = MagicMock()
    pool.available_backends = ["claude"]
    pool.run_turn = run_turn
    pool.cancel = AsyncMock()
    pool.close_all = AsyncMock()
    pool.release_resident = AsyncMock()
    return pool


async def _close_chat_one(registry: ChatRuntimePoolRegistry, pool: MagicMock) -> None:
    """Acquire chat-1's pool, then tear the chat down (deleted conversation) so the pool is unregistered."""

    async def build_pool() -> MagicMock:
        return pool

    await registry.acquire("chat-1", "fp-a", build_pool)
    await registry.close_chat("chat-1")
    pool.close_all.assert_awaited_once()
    assert "chat-1" not in registry._entries


@pytest.mark.asyncio
async def test_ensure_tracked_fast_path_does_not_wait_for_registry_lock() -> None:
    """A turn on a registered pool must not queue behind another chat's slow pool close."""
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)
    pool = _build_turn_pool("p", [])

    async def build_pool() -> MagicMock:
        return pool

    await registry.acquire("chat-1", "fp-a", build_pool)
    async with registry._lock:
        tracked = await asyncio.wait_for(registry.ensure_tracked("chat-1", "fp-a", pool), timeout=0.5)

    assert tracked is pool


@pytest.mark.asyncio
async def test_ensure_tracked_readopts_closed_pool_so_shutdown_closes_it() -> None:
    """A closed pool revives its backends on the next turn; they must stay reachable by close_all."""
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)
    pool = _build_turn_pool("p", [])
    await _close_chat_one(registry, pool)

    tracked = await registry.ensure_tracked("chat-1", "fp-a", pool)

    assert tracked is pool
    assert registry._entries["chat-1"].pool is pool
    await registry.close_all()
    assert pool.close_all.await_count == 2


@pytest.mark.asyncio
async def test_ensure_tracked_prefers_the_registered_pool_over_a_stale_one() -> None:
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)
    current = _build_turn_pool("current", [])
    stale = _build_turn_pool("stale", [])

    async def build_pool() -> MagicMock:
        return current

    await registry.acquire("chat-1", "fp-a", build_pool)

    assert await registry.ensure_tracked("chat-1", "fp-a", stale) is current


@pytest.mark.asyncio
async def test_facade_run_turn_readopts_pool_closed_while_the_unit_stayed_cached() -> None:
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)
    ran_on: list[str] = []
    pool = _build_turn_pool("p", ran_on)
    facade = ChatScopedRuntimePoolFacade(pool, "chat-1", registry, "fp-a")
    await _close_chat_one(registry, pool)

    async for _ in facade.run_turn("claude", "hi", "s1", workspace=_WORKSPACE):
        pass

    assert ran_on == ["p"]
    assert registry._entries["chat-1"].pool is pool
    await registry.close_all()
    assert pool.close_all.await_count == 2


@pytest.mark.asyncio
async def test_facade_follows_the_registered_pool_for_turns_and_cancel() -> None:
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)
    ran_on: list[str] = []
    current = _build_turn_pool("current", ran_on)
    stale = _build_turn_pool("stale", ran_on)

    async def build_pool() -> MagicMock:
        return current

    await registry.acquire("chat-1", "fp-a", build_pool)
    facade = ChatScopedRuntimePoolFacade(stale, "chat-1", registry, "fp-a")

    async for _ in facade.run_turn("claude", "hi", "s1", workspace=_WORKSPACE):
        pass
    await facade.cancel("claude", "s1")

    assert ran_on == ["current"]
    current.cancel.assert_awaited_once_with("claude", "s1")
    stale.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_facade_hands_the_turn_images_to_the_pool() -> None:
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)
    seen: list[list[str] | None] = []
    pool = _build_turn_pool("p", [])

    async def run_turn(name: str, prompt: str, session_id: str, *, images: list[str] | None = None, **_: object):
        seen.append(images)
        yield MagicMock()

    pool.run_turn = run_turn
    facade = ChatScopedRuntimePoolFacade(pool, "chat-1", registry, "fp-a")

    async for _ in facade.run_turn("claude", "hi", "s1", workspace=_WORKSPACE, images=["/state/a.png"]):
        pass
    async for _ in facade.run_turn("claude", "hi", "s1", workspace=_WORKSPACE):
        pass

    assert seen == [["/state/a.png"], None]


@pytest.mark.asyncio
async def test_facade_hands_the_turn_workspace_to_the_pool() -> None:
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)
    seen: list[str] = []
    pool = _build_turn_pool("p", [])

    async def run_turn(name: str, prompt: str, session_id: str, *, workspace: str, **_: object):
        seen.append(workspace)
        yield MagicMock()

    pool.run_turn = run_turn
    facade = ChatScopedRuntimePoolFacade(pool, "chat-1", registry, "fp-a")

    async for _ in facade.run_turn("claude", "hi", "s1", workspace="/work/a"):
        pass
    async for _ in facade.run_turn("claude", "hi", "s1", workspace="/work/b"):
        pass

    assert seen == ["/work/a", "/work/b"]


@pytest.mark.asyncio
async def test_turn_arriving_during_an_idle_release_waits_for_it() -> None:
    """The release closes ACP processes; a turn that starts meanwhile must run only after it finished."""
    registry = ChatRuntimePoolRegistry(idle_seconds=0.01)
    events: list[str] = []
    release_gate = asyncio.Event()
    pool = _build_turn_pool("p", events)

    async def slow_release() -> None:
        events.append("release_begin")
        await release_gate.wait()
        events.append("release_end")

    pool.release_resident = AsyncMock(side_effect=slow_release)
    facade = ChatScopedRuntimePoolFacade(pool, "chat-1", registry, "fp-a")

    async def build_pool() -> MagicMock:
        return pool

    await registry.acquire("chat-1", "fp-a", build_pool)
    await asyncio.sleep(0.02)
    releasing = asyncio.create_task(registry.acquire("chat-2", "fp-a", _build_mock_pool))
    await _wait_for(lambda: "release_begin" in events)

    async def deliver() -> None:
        async for _ in facade.run_turn("claude", "hi", "s1", workspace=_WORKSPACE):
            pass

    turn = asyncio.create_task(deliver())
    await asyncio.sleep(0.05)
    assert events == ["release_begin"]

    release_gate.set()
    await asyncio.gather(releasing, turn)
    assert events == ["release_begin", "release_end", "p"]


async def _wait_for(condition: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)
