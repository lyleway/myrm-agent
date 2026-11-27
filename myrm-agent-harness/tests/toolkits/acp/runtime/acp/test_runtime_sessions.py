"""AcpRuntime gives a conversation back to the agent after the agent was released, by what the agent advertised.

The scripted agent keeps its sessions in a state file, so a second agent process still knows them, and answers
``/history`` with the prompts the session has seen: what a conversation that continued (or did not) looks like from the
outside. Releasing the pool's resident backends ends the agent process, as an idle release does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    AcpErrorCode,
    CliSessionRecord,
    ContinuityStatus,
    RuntimeEvent,
    RuntimeEventType,
)

from ._fakes import MemorySessionStore, continuity_notices, make_pool, run_turn

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the scripted ACP agent is exercised on POSIX only")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def state(tmp_path: Path) -> list[str]:
    """The agent arguments that make it keep its sessions on disk."""
    return ["--state-file", str(tmp_path / "agent-state.json")]


async def _say(
    pool: RuntimePool, prompt: str, workspace: Path, session_id: str = "chat-1"
) -> tuple[str, list[RuntimeEvent]]:
    return await run_turn(pool, [], workspace=workspace, prompt=prompt, session_id=session_id)


class TestContinuingAConversation:
    async def test_a_new_agent_process_resumes_the_session_the_agent_advertised_it_can_resume(
        self, workspace: Path, state: list[str]
    ) -> None:
        pool = make_pool("--resume", *state, session_store=MemorySessionStore())
        try:
            await _say(pool, "one", workspace)
            await pool.release_resident()
            text, events = await _say(pool, "/history", workspace)
        finally:
            await pool.close_all()

        assert text == "[history] one"
        assert continuity_notices(events) == []

    async def test_an_agent_that_can_only_load_a_session_gets_it_back_without_its_replay_in_the_output(
        self, workspace: Path, state: list[str]
    ) -> None:
        pool = make_pool("--load", *state, session_store=MemorySessionStore())
        try:
            await _say(pool, "one", workspace)
            await pool.release_resident()
            text, events = await _say(pool, "/history", workspace)
        finally:
            await pool.close_all()

        # The agent replayed "one" and its echo while loading; none of that is this turn's output.
        assert text == "[history] one"
        assert continuity_notices(events) == []

    async def test_resuming_wins_over_loading_when_the_agent_advertised_both(
        self, workspace: Path, state: list[str]
    ) -> None:
        pool = make_pool("--resume", "--load", *state, session_store=MemorySessionStore())
        try:
            await _say(pool, "one", workspace)
            await pool.release_resident()
            text, _ = await _say(pool, "/history", workspace)
        finally:
            await pool.close_all()

        assert text == "[history] one"

    async def test_an_agent_that_advertised_neither_starts_afresh_and_nothing_is_recorded_for_it(
        self, workspace: Path, state: list[str]
    ) -> None:
        store = MemorySessionStore()
        pool = make_pool(*state, session_store=store)
        try:
            await _say(pool, "one", workspace)
            await pool.release_resident()
            text, events = await _say(pool, "/history", workspace)
        finally:
            await pool.close_all()

        assert text == "[history] "
        assert continuity_notices(events) == []
        assert store.saved == []

    async def test_the_session_of_a_running_agent_is_kept_without_opening_it_again(
        self, workspace: Path, state: list[str]
    ) -> None:
        store = MemorySessionStore()
        pool = make_pool("--resume", *state, session_store=store)
        try:
            await _say(pool, "one", workspace)
            text, events = await _say(pool, "/history", workspace)
        finally:
            await pool.close_all()

        assert text == "[history] one"
        assert len(store.saved) == 1
        assert {event.session_id for event in events} == {"chat-1"}

    async def test_one_agent_process_keeps_a_session_for_each_conversation(
        self, workspace: Path, state: list[str]
    ) -> None:
        pool = make_pool("--resume", *state, session_store=MemorySessionStore())
        try:
            await _say(pool, "alpha", workspace, "a")
            await _say(pool, "beta", workspace, "b")
            first, _ = await _say(pool, "/history", workspace, "a")
            second, _ = await _say(pool, "/history", workspace, "b")
        finally:
            await pool.close_all()

        assert (first, second) == ("[history] alpha", "[history] beta")

    async def test_without_a_store_a_released_agent_starts_a_new_conversation(
        self, workspace: Path, state: list[str]
    ) -> None:
        pool = make_pool("--resume", *state)
        try:
            await _say(pool, "one", workspace)
            await pool.release_resident()
            text, events = await _say(pool, "/history", workspace)
        finally:
            await pool.close_all()

        assert text == "[history] "
        assert continuity_notices(events) == []


class TestAConversationThatCannotContinue:
    async def test_a_session_the_agent_no_longer_knows_is_replaced_and_said_so(
        self, workspace: Path, tmp_path: Path, state: list[str]
    ) -> None:
        store = MemorySessionStore()
        pool = make_pool("--resume", *state, session_store=store)
        try:
            await _say(pool, "one", workspace)
            first = store.records["chat-1"]
            await pool.release_resident()
            (tmp_path / "agent-state.json").unlink()
            text, events = await _say(pool, "/history", workspace)
            replacement = store.records["chat-1"]
            await pool.release_resident()
            afterwards, later_events = await _say(pool, "/history", workspace)
        finally:
            await pool.close_all()

        assert text == "[history] "
        assert continuity_notices(events) == [ContinuityStatus.SESSION_EXPIRED]
        assert replacement.cli_session_id != first.cli_session_id
        # The replacement is the conversation from now on.
        assert afterwards == "[history] /history"
        assert continuity_notices(later_events) == []

    async def test_a_session_recorded_in_another_folder_is_not_continued(
        self, tmp_path: Path, state: list[str]
    ) -> None:
        here, there = tmp_path / "here", tmp_path / "there"
        here.mkdir()
        there.mkdir()
        store = MemorySessionStore()
        pool = make_pool("--resume", *state, session_store=store)
        try:
            await _say(pool, "one", here)
            await pool.release_resident()
            text, events = await _say(pool, "/history", there)
        finally:
            await pool.close_all()

        assert text == "[history] "
        assert continuity_notices(events) == [ContinuityStatus.WORKSPACE_CHANGED]
        assert store.records["chat-1"].cwd == str(there)

    async def test_a_login_the_agent_asks_for_while_continuing_fails_the_turn_and_keeps_the_record(
        self, workspace: Path, state: list[str]
    ) -> None:
        store = MemorySessionStore()
        pool = make_pool("--resume", *state, session_store=store)
        try:
            await _say(pool, "one", workspace)
            recorded = store.records["chat-1"]
            await pool.release_resident()
        finally:
            await pool.close_all()
        asking = make_pool("--resume", "--continue-needs-login", *state, session_store=store)
        try:
            text, events = await _say(asking, "/history", workspace)
        finally:
            await asking.close_all()

        errors = [e.data["error"] for e in events if e.type is RuntimeEventType.ERROR]
        assert [getattr(error, "code", None) for error in errors] == [AcpErrorCode.AUTH_FAILED]
        assert text == ""
        assert store.records["chat-1"] == recorded

    async def test_a_store_that_fails_costs_the_continuity_and_never_the_turn(
        self, workspace: Path, state: list[str]
    ) -> None:
        class BrokenStore:
            async def load(self, session_id: str) -> CliSessionRecord | None:
                raise OSError("disk gone")

            async def save(self, session_id: str, record: CliSessionRecord) -> None:
                raise OSError("disk gone")

        pool = make_pool("--resume", *state, session_store=BrokenStore())
        try:
            await _say(pool, "one", workspace)
            await pool.release_resident()
            text, events = await _say(pool, "/history", workspace)
        finally:
            await pool.close_all()

        assert text == "[history] "
        assert RuntimeEventType.ERROR not in {event.type for event in events}
