"""A real Gemini or Qwen, driven over ACP, keeps the conversation it had before its process ended.

Gemini can only load a stored session and Qwen can resume one; the host picks by what each advertised and the person
sees the same thing either way: the agent still knows what was said, nothing it said before is said again, and a
conversation the agent no longer has is replaced by a new one with a notice.

The model is scripted (``FakeGeminiApi``, ``FakeOpenAiChatApi``), so what an agent remembers is read off what it sends
the model. Run with ``-m integration``. An agent that is not installed skips its tests, unless
``MYRM_CONTRACT_REQUIRE_CLIS`` names it. No login and no real model are involved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.types import ContinuityStatus
from tests.toolkits.acp._fake_model_apis import ScriptedModelApi

from ._fakes import MemorySessionStore, continuity_notices, run_turn
from ._real_agents import SCRIPTED_AGENTS, scripted_pool

pytestmark = pytest.mark.integration

_AGENT_NAMES = sorted(SCRIPTED_AGENTS)
_FIRST_WORDS = "remember: the code word is pineapple"
_SECOND_WORDS = "what is the code word?"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def _home(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


def _told_the_model(api: ScriptedModelApi, since: int, word: str) -> bool:
    """Whether any request the agent made after the first ``since`` carried ``word`` to the model."""
    return any(word in json.dumps(body) for body in api.requests[since:])


@pytest.mark.parametrize("agent", _AGENT_NAMES)
async def test_a_new_agent_process_continues_the_conversation_without_saying_it_again(
    agent: str, workspace: Path, tmp_path: Path
) -> None:
    async with SCRIPTED_AGENTS[agent].api(final_text="first reply") as api:
        pool = scripted_pool(agent, api, _home(tmp_path, "home"), session_store=MemorySessionStore())
        try:
            first, _ = await run_turn(pool, [], workspace=workspace, prompt=_FIRST_WORDS, agent=agent)
            await pool.release_resident()
            earlier_requests = len(api.requests)
            api.final_text = "second reply"
            second, events = await run_turn(pool, [], workspace=workspace, prompt=_SECOND_WORDS, agent=agent)
        finally:
            await pool.close_all()

    assert first == "first reply"
    # An agent that replays its history to a client that is loading it would put the first reply in front of this one.
    assert second == "second reply"
    assert continuity_notices(events) == []
    assert _told_the_model(api, earlier_requests, "pineapple")


@pytest.mark.parametrize("agent", _AGENT_NAMES)
async def test_a_conversation_the_agent_no_longer_has_is_replaced_by_a_new_one_with_a_notice(
    agent: str, workspace: Path, tmp_path: Path
) -> None:
    store = MemorySessionStore()
    async with SCRIPTED_AGENTS[agent].api(final_text="first reply") as api:
        first_pool = scripted_pool(agent, api, _home(tmp_path, "home"), session_store=store)
        try:
            await run_turn(first_pool, [], workspace=workspace, prompt=_FIRST_WORDS, agent=agent)
        finally:
            await first_pool.close_all()
        earlier_requests = len(api.requests)

        # Another agent home knows nothing of the session the store still points at.
        api.final_text = "second reply"
        second_pool = scripted_pool(agent, api, _home(tmp_path, "other-home"), session_store=store)
        try:
            second, events = await run_turn(second_pool, [], workspace=workspace, prompt=_SECOND_WORDS, agent=agent)
        finally:
            await second_pool.close_all()

    assert second == "second reply"
    assert continuity_notices(events) == [ContinuityStatus.SESSION_EXPIRED]
    assert not _told_the_model(api, earlier_requests, "pineapple")
