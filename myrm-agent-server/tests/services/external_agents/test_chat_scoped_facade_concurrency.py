"""Which delegation turns of one chat run side by side through the chat-scoped facade, on a real RuntimePool.

A backend holds one process and one session, so turns that share a backend queue up; turns of different agents
share nothing and must not wait for each other.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from unittest.mock import MagicMock

import pytest
from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    BackendCapabilities,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
    create_event,
)

from app.services.external_agents.runtime_pool_registry import (
    ChatRuntimePoolRegistry,
    ChatScopedRuntimePoolFacade,
)

_WORKSPACE = "/work/repo"
_CHAT = "chat-1"


class _OpenTurns:
    """A pool whose backends keep every turn open until ``release`` is set, counting the turns open at once."""

    def __init__(self, *names: str) -> None:
        self.release = asyncio.Event()
        self.open_now = dict.fromkeys(names, 0)
        self.peak = dict.fromkeys(names, 0)
        self.pool = RuntimePool(max_concurrent=4)
        backends = {name: self._backend(name) for name in names}
        for name in names:
            self.pool.register(name, RuntimeConfig(backend_type="cli", command=name))
        self.pool.get = MagicMock(side_effect=lambda name, workspace: backends[name])  # type: ignore[method-assign]

    @property
    def open_total(self) -> int:
        return sum(self.open_now.values())

    def _backend(self, name: str) -> MagicMock:
        async def run_turn(prompt: str, session_id: str, **_: object) -> AsyncGenerator[RuntimeEvent]:
            self.open_now[name] += 1
            self.peak[name] = max(self.peak[name], self.open_now[name])
            try:
                await self.release.wait()
                yield create_event(RuntimeEventType.DONE, session_id, stop_reason="end_turn")
            finally:
                self.open_now[name] -= 1

        backend = MagicMock()
        backend.capabilities = BackendCapabilities()
        backend.run_turn = run_turn
        return backend


async def _facade(turns: _OpenTurns) -> ChatScopedRuntimePoolFacade:
    registry = ChatRuntimePoolRegistry(idle_seconds=3600.0)

    async def build_pool() -> RuntimePool:
        return turns.pool

    await registry.acquire(_CHAT, "fp-a", build_pool)
    return ChatScopedRuntimePoolFacade(turns.pool, _CHAT, registry, "fp-a")


async def _drain(facade: ChatScopedRuntimePoolFacade, agent: str, session_id: str) -> None:
    async for _ in facade.run_turn(agent, "go", session_id, workspace=_WORKSPACE):
        pass


async def _wait_for(condition: Callable[[], bool], *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_turns_of_different_agents_in_one_chat_run_side_by_side() -> None:
    turns = _OpenTurns("claude", "codex")
    facade = await _facade(turns)

    running = [
        asyncio.create_task(_drain(facade, "claude", f"claude-{_CHAT}")),
        asyncio.create_task(_drain(facade, "codex", f"codex-{_CHAT}")),
    ]
    await _wait_for(lambda: turns.open_total == 2)

    turns.release.set()
    await asyncio.gather(*running)


@pytest.mark.asyncio
async def test_turns_of_one_agent_in_one_chat_wait_for_each_other() -> None:
    turns = _OpenTurns("claude")
    facade = await _facade(turns)

    running = [
        asyncio.create_task(_drain(facade, "claude", f"claude-{_CHAT}")),
        asyncio.create_task(_drain(facade, "claude", f"claude-{_CHAT}-second")),
    ]
    await _wait_for(lambda: turns.open_total == 1)
    await asyncio.sleep(0.05)
    assert turns.open_total == 1

    turns.release.set()
    await asyncio.gather(*running)
    assert turns.peak["claude"] == 1


@pytest.mark.asyncio
async def test_a_turn_of_one_agent_does_not_hold_back_another_agents_queue() -> None:
    """Two claude turns queue behind each other; codex still starts at once."""
    turns = _OpenTurns("claude", "codex")
    facade = await _facade(turns)

    running = [
        asyncio.create_task(_drain(facade, "claude", f"claude-{_CHAT}")),
        asyncio.create_task(_drain(facade, "claude", f"claude-{_CHAT}-second")),
    ]
    await _wait_for(lambda: turns.open_now["claude"] == 1)
    running.append(asyncio.create_task(_drain(facade, "codex", f"codex-{_CHAT}")))
    await _wait_for(lambda: turns.open_now["codex"] == 1)

    turns.release.set()
    await asyncio.gather(*running)
    assert turns.peak == {"claude": 1, "codex": 1}
