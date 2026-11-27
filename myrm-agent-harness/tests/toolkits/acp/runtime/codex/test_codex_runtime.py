"""``CodexRuntime`` through ``RuntimePool`` against a scripted app server: the lifecycle a real Codex cannot be made to show.

A server that dies mid-turn, stays silent, refuses a request, ignores an interrupt or speaks for another turn; the
approvals, the login, the thread bookkeeping and the process supervision around them. What a real Codex does is
covered by the ``test_real_codex_*`` tests.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    AcpErrorCode,
    CliSessionRecord,
    McpServerConfig,
    RuntimeEvent,
    RuntimeEventType,
)
from tests.toolkits.acp._brokers import AnsweringBroker
from tests.toolkits.acp._stores import RecordingStore
from tests.toolkits.acp.runtime.codex._real import ended_cleanly, errors_of, failure_of, run_turn, text_of
from tests.toolkits.acp.runtime.codex._scripted import ScriptedServer, ask, complete, notify, say

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in server is a POSIX shebang script")

_COMMAND_APPROVAL = "item/commandExecution/requestApproval"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def _statuses(events: list[RuntimeEvent]) -> list[str]:
    return [str(e.data["status"]) for e in events if e.type is RuntimeEventType.STATUS_UPDATE]


def _error_code(events: list[RuntimeEvent]) -> AcpErrorCode:
    (error,) = errors_of(events)
    return failure_of(error).code


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _gone(pid: int) -> bool:
    for _ in range(100):
        if not _alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


# -- a turn and its thread ---------------------------------------------------------------------------------------------


async def test_a_turn_streams_what_the_server_says_and_ends_with_done(tmp_path: Path, workspace: Path) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[say("hel"), say("lo"), complete()]]})
    pool = server.pool()
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert ended_cleanly(events)
    assert text_of(events) == "hello"
    assert _statuses(events) == ["starting"]
    assert server.starts()[0]["argv"] == ["app-server"]
    (turn,) = server.received("turn/start")
    assert turn["input"] == [{"type": "text", "text": "do the scripted work", "text_elements": []}]
    assert (turn["approvalPolicy"], turn["sandboxPolicy"]) == ("never", {"type": "readOnly", "networkAccess": False})


async def test_an_agent_registered_as_an_app_server_runs_the_same_way_as_one_stored_as_a_cli(
    tmp_path: Path, workspace: Path
) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[say("hi"), complete()]]})
    pool = server.pool(backend_type="app_server")
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert ended_cleanly(events)
    assert text_of(events) == "hi"


async def test_with_somebody_to_ask_a_safe_turn_runs_under_the_read_only_preset(
    tmp_path: Path, workspace: Path
) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[complete()]]})
    pool = server.pool()
    try:
        await run_turn(pool, workspace, broker=AnsweringBroker(True))
    finally:
        await pool.close_all()

    (thread,) = server.received("thread/start")
    (turn,) = server.received("turn/start")
    assert (thread["approvalPolicy"], thread["sandbox"], thread["ephemeral"]) == ("on-request", "read-only", True)
    assert turn["approvalPolicy"] == "on-request"


async def test_a_session_keeps_its_server_and_its_thread_and_another_session_gets_its_own(
    tmp_path: Path, workspace: Path
) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[complete()]]})
    pool = server.pool()
    try:
        for session in ("a", "a", "b"):
            assert ended_cleanly(await run_turn(pool, workspace, session_id=session))
    finally:
        await pool.close_all()

    assert len(server.starts()) == 1
    assert len(server.received("initialize")) == 1
    assert server.received("thread/resume") == []
    assert len(server.received("thread/start")) == 2
    assert [t["threadId"] for t in server.received("turn/start")] == ["thread-1", "thread-1", "thread-2"]


async def test_images_travel_as_local_files_and_never_as_arguments(tmp_path: Path, workspace: Path) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[complete()]]})
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")
    pool = server.pool()
    try:
        await run_turn(pool, workspace, images=[str(image)])
    finally:
        await pool.close_all()

    (turn,) = server.received("turn/start")
    assert turn["input"][1] == {"type": "localImage", "path": str(image)}  # type: ignore[index]
    assert server.starts()[0]["argv"] == ["app-server"]


async def test_the_mcp_servers_of_the_host_are_offered_to_the_thread_under_valid_names(
    tmp_path: Path, workspace: Path
) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[complete()]]})
    pool = server.pool(mcp_servers=[McpServerConfig(name="my server.1", command="mcp", args=["--x"], env={"A": "b"})])
    try:
        await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    (thread,) = server.received("thread/start")
    assert thread["config"] == {"mcp_servers": {"my_server_1": {"command": "mcp", "args": ["--x"], "env": {"A": "b"}}}}


async def test_the_notifications_of_another_turn_are_not_this_turns(tmp_path: Path, workspace: Path) -> None:
    other_turn_ends = {
        "notify": {
            "method": "turn/completed",
            "params": {"threadId": "$thread", "turn": {"id": "turn-other", "status": "failed", "items": []}},
        }
    }
    steps = [notify("item/agentMessage/delta", turnId="turn-other", itemId="x", delta="STALE"), other_turn_ends]
    server = ScriptedServer(tmp_path / "s", {"turns": [[*steps, say("real"), complete()]]})
    pool = server.pool()
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert ended_cleanly(events)
    assert text_of(events) == "real"


async def test_a_failed_turn_ends_with_one_error_that_carries_the_servers_words(
    tmp_path: Path, workspace: Path
) -> None:
    failure = {"message": "The usage limit was reached", "codexErrorInfo": "usageLimitExceeded"}
    server = ScriptedServer(tmp_path / "s", {"turns": [[say("par"), complete("failed", failure)]]})
    pool = server.pool()
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert _error_code(events) is AcpErrorCode.UNKNOWN
    assert failure_of(errors_of(events)[0]).message == "The usage limit was reached"
    assert RuntimeEventType.DONE not in [e.type for e in events]


# -- a server that goes wrong ------------------------------------------------------------------------------------------


async def test_a_server_that_dies_mid_turn_is_reported_and_the_next_turn_resumes_on_a_new_one(
    tmp_path: Path, workspace: Path
) -> None:
    store = RecordingStore()
    dying = {"turns": [[say("par"), {"exit": 3, "stderr": "boom: lost the plot"}]]}
    server = ScriptedServer(tmp_path / "s", {"processes": [dying, {"turns": [[say("back"), complete()]]}]})
    pool = server.pool(store=store)
    try:
        first = await run_turn(pool, workspace)
        second = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    (error,) = errors_of(first)
    failure = failure_of(error)
    assert failure.code is AcpErrorCode.PROCESS_CRASHED
    assert failure.retryable
    assert "code 3" in failure.message
    assert "boom: lost the plot" in failure.message
    assert ended_cleanly(second)
    assert len(server.starts()) == 2
    assert [(r["threadId"], r["excludeTurns"]) for r in server.received("thread/resume")] == [("thread-1", True)]


async def test_a_server_that_exits_during_the_handshake_says_why(tmp_path: Path, workspace: Path) -> None:
    server = ScriptedServer(tmp_path / "s", {"handshake": "exit", "exit_code": 7, "stderr": "no codex home"})
    pool = server.pool()
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    (error,) = errors_of(events)
    failure = failure_of(error)
    assert failure.code is AcpErrorCode.PROCESS_CRASHED
    assert "code 7" in failure.message
    assert "no codex home" in failure.message


async def test_a_silent_server_fails_the_handshake_and_is_stopped(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("myrm_agent_harness.toolkits.acp.runtime.codex.connection._HANDSHAKE_SECONDS", 0.5)
    server = ScriptedServer(tmp_path / "s", {"handshake": "silent"})
    pool = server.pool()
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    (error,) = errors_of(events)
    failure = failure_of(error)
    assert failure.code is AcpErrorCode.PROCESS_CRASHED
    assert "did not answer the handshake" in failure.message
    assert await _gone(int(server.starts()[0]["pid"]))  # type: ignore[call-overload]


async def test_a_refused_turn_reports_the_servers_words_and_the_server_stays(tmp_path: Path, workspace: Path) -> None:
    refusal = {"refuse": {"code": -32600, "message": "The model is not available"}}
    server = ScriptedServer(tmp_path / "s", {"turns": [[refusal], [complete()]]})
    pool = server.pool()
    try:
        first = await run_turn(pool, workspace)
        second = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    (error,) = errors_of(first)
    failure = failure_of(error)
    assert failure.code is AcpErrorCode.UNKNOWN
    assert "The model is not available" in failure.message
    assert ended_cleanly(second)
    assert len(server.starts()) == 1


async def test_a_released_pool_stops_the_server_process(tmp_path: Path, workspace: Path) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[complete()]]})
    pool = server.pool()
    try:
        await run_turn(pool, workspace)
        pid = int(server.starts()[0]["pid"])  # type: ignore[call-overload]
        assert _alive(pid)
        await pool.release_resident()
        assert await _gone(pid)
        await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert len(server.starts()) == 2


# -- the login -----------------------------------------------------------------------------------------------------------


async def test_an_api_key_is_given_to_the_server_by_login_and_kept_out_of_its_environment(
    tmp_path: Path, workspace: Path
) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[complete()]]})
    pool = server.pool(auth_mode="api_key", env={"OPENAI_API_KEY": "sk-secret", "CODEX_API_KEY": "sk-other"})
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    start = server.starts()[0]
    environment = start["env"]
    assert isinstance(environment, dict)
    assert ended_cleanly(events)
    assert server.received("account/login/start") == [{"type": "apiKey", "apiKey": "sk-secret"}]
    assert not {"OPENAI_API_KEY", "CODEX_API_KEY"} & set(environment)
    assert start["argv"] == ["app-server", "-c", 'cli_auth_credentials_store="ephemeral"']


async def test_a_refused_login_fails_the_start_without_repeating_the_key(tmp_path: Path, workspace: Path) -> None:
    server = ScriptedServer(tmp_path / "s", {"login": "refuse"})
    pool = server.pool(auth_mode="api_key", env={"OPENAI_API_KEY": "sk-secret"})
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    (error,) = errors_of(events)
    failure = failure_of(error)
    assert failure.code is AcpErrorCode.PROCESS_CRASHED
    assert failure.message == "The Codex app server refused the handshake: The key is not valid"


async def test_a_subscription_agent_logs_in_with_nothing(tmp_path: Path, workspace: Path) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[complete()]]})
    pool = server.pool()
    try:
        await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert server.received("account/login/start") == []
    assert server.starts()[0]["argv"] == ["app-server"]


# -- the thread of a session across processes ------------------------------------------------------------------------------


async def test_a_thread_recorded_for_another_directory_is_left_and_the_conversation_starts_over(
    tmp_path: Path, workspace: Path
) -> None:
    store = RecordingStore()
    store.records["chat-1"] = CliSessionRecord("thread-9", str(tmp_path / "elsewhere"))
    server = ScriptedServer(tmp_path / "s", {"turns": [[complete()]]})
    pool = server.pool(store=store)
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert ended_cleanly(events)
    assert "session_workspace_changed" in _statuses(events)
    assert server.received("thread/resume") == []
    assert store.records["chat-1"].cli_session_id == "thread-1"


async def test_a_thread_the_server_no_longer_knows_is_replaced_and_the_record_follows(
    tmp_path: Path, workspace: Path
) -> None:
    store = RecordingStore()
    store.records["chat-1"] = CliSessionRecord("thread-old", str(workspace.resolve()))
    server = ScriptedServer(tmp_path / "s", {"resume": "unknown", "turns": [[complete()]]})
    pool = server.pool(store=store)
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert ended_cleanly(events)
    assert "session_expired" in _statuses(events)
    assert [r["threadId"] for r in server.received("thread/resume")] == ["thread-old"]
    assert store.records["chat-1"].cli_session_id == "thread-1"


async def test_with_a_store_threads_are_kept_in_the_users_codex_history(tmp_path: Path, workspace: Path) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[complete()]]})
    pool = server.pool(store=RecordingStore())
    try:
        await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    (thread,) = server.received("thread/start")
    assert "ephemeral" not in thread


# -- the sandbox the user configured ---------------------------------------------------------------------------------------


async def test_allow_all_runs_in_the_sandbox_the_user_configured(tmp_path: Path, workspace: Path) -> None:
    settings = {"writable_roots": ["/data"], "network_access": True, "exclude_slash_tmp": True}
    server = ScriptedServer(tmp_path / "s", {"config": {"sandbox_workspace_write": settings}, "turns": [[complete()]]})
    pool = server.pool(mode="allow_all")
    try:
        await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    (read,) = server.received("config/read")
    (turn,) = server.received("turn/start")
    assert read["cwd"] == str(workspace.resolve())
    assert turn["approvalPolicy"] == "never"
    assert turn["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": ["/data"],
        "networkAccess": True,
        "excludeTmpdirEnvVar": False,
        "excludeSlashTmp": True,
    }


async def test_a_safe_turn_does_not_read_the_users_config(tmp_path: Path, workspace: Path) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[complete()]]})
    pool = server.pool()
    try:
        await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert server.received("config/read") == []


# -- the approvals ---------------------------------------------------------------------------------------------------------


def _command_request() -> dict[str, object]:
    return ask(_COMMAND_APPROVAL, itemId="cmd-1", command="/bin/zsh -lc 'rm -rf build'", cwd="/w", reason="clean up")


async def test_an_approval_request_goes_to_the_broker_and_its_verdict_back_to_the_server(
    tmp_path: Path, workspace: Path
) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[_command_request(), _command_request(), complete()]]})
    broker = AnsweringBroker(True)
    pool = server.pool()
    try:
        events = await run_turn(pool, workspace, broker=broker)
    finally:
        await pool.close_all()

    assert ended_cleanly(events)
    assert server.answers() == [{"decision": "accept"}, {"decision": "accept"}]
    (first, _second) = broker.asked
    assert (first.agent, first.session_id, first.kind, first.tool_call_id) == ("codex", "chat-1", "execute", "cmd-1")
    assert first.input == {"command": "/bin/zsh -lc 'rm -rf build'", "cwd": "/w", "reason": "clean up"}


@pytest.mark.parametrize(
    ("mode", "broker"),
    [("safe", AnsweringBroker(False)), ("safe", None), ("allow_all", AnsweringBroker(True))],
    ids=["refused", "nobody to ask", "allow_all asks nobody"],
)
async def test_a_request_nobody_allows_is_declined_and_the_turn_goes_on(
    tmp_path: Path, workspace: Path, mode: str, broker: AnsweringBroker | None
) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[_command_request(), complete()]]})
    pool = server.pool(mode=mode)  # type: ignore[arg-type]
    try:
        events = await run_turn(pool, workspace, broker=broker)
    finally:
        await pool.close_all()

    assert ended_cleanly(events)
    assert server.answers() == [{"decision": "decline"}]
    if mode == "allow_all":
        assert broker is not None
        assert broker.asked == []


async def test_a_request_the_host_does_not_know_is_answered_with_method_not_found(
    tmp_path: Path, workspace: Path
) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[ask("item/never/heardOf"), complete()]]})
    pool = server.pool()
    try:
        events = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert ended_cleanly(events)
    assert server.answers() == [{"code": -32601, "message": "Method not supported: item/never/heardOf"}]


# -- stopping ---------------------------------------------------------------------------------------------------------------


async def _collect_until_text(
    pool: RuntimePool, workspace: Path, session: str = "chat-1"
) -> tuple[asyncio.Task[None], list[RuntimeEvent]]:
    """Start a turn in the background and return once it has streamed its first text."""
    events: list[RuntimeEvent] = []

    async def drive() -> None:
        async for event in pool.run_turn("codex", "go", session, workspace=str(workspace)):
            events.append(event)

    task = asyncio.create_task(drive())
    async with asyncio.timeout(30):
        while not any(e.type is RuntimeEventType.TEXT_DELTA for e in events):
            await asyncio.sleep(0.02)
    return task, events


async def test_a_stop_interrupts_the_turn_and_the_same_server_serves_the_next(tmp_path: Path, workspace: Path) -> None:
    waiting = [say("working"), {"wait_interrupt": True}, complete("interrupted")]
    server = ScriptedServer(tmp_path / "s", {"turns": [waiting, [say("again"), complete()]]})
    pool = server.pool()
    try:
        task, events = await _collect_until_text(pool, workspace)
        await pool.cancel("codex", "chat-1")
        await asyncio.wait_for(task, 20)
        second = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert _error_code(events) is AcpErrorCode.CANCELLED
    assert server.received("turn/interrupt") == [{"threadId": "thread-1", "turnId": "turn-1"}]
    assert ended_cleanly(second)
    assert len(server.starts()) == 1


async def test_a_stop_that_is_not_obeyed_ends_the_server_and_the_next_turn_starts_another(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("myrm_agent_harness.toolkits.acp.runtime.codex.runtime._INTERRUPT_SECONDS", 0.3)
    monkeypatch.setattr("myrm_agent_harness.toolkits.acp.runtime.codex.runtime._STOP_GRACE_SECONDS", 0.6)
    stuck = {"turns": [[say("stuck"), {"hang": True}]]}
    server = ScriptedServer(tmp_path / "s", {"processes": [stuck, {"turns": [[complete()]]}]})
    pool = server.pool()
    try:
        task, events = await _collect_until_text(pool, workspace)
        await pool.cancel("codex", "chat-1")
        await asyncio.wait_for(task, 20)
        second = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert _error_code(events) is AcpErrorCode.CANCELLED
    assert await _gone(int(server.starts()[0]["pid"]))  # type: ignore[call-overload]
    assert ended_cleanly(second)
    assert len(server.starts()) == 2


async def test_the_time_limit_of_a_turn_interrupts_it_and_the_server_stays(tmp_path: Path, workspace: Path) -> None:
    waiting = [say("slow"), {"wait_interrupt": True}, complete("interrupted")]
    server = ScriptedServer(tmp_path / "s", {"turns": [waiting, [complete()]]})
    pool = server.pool(timeout_seconds=1)
    try:
        first = await run_turn(pool, workspace)
        second = await run_turn(pool, workspace)
    finally:
        await pool.close_all()

    assert _error_code(first) is AcpErrorCode.TIMEOUT
    assert len(server.received("turn/interrupt")) == 1
    assert ended_cleanly(second)
    assert len(server.starts()) == 1


async def test_a_stop_while_a_request_waits_answers_it_with_cancel(tmp_path: Path, workspace: Path) -> None:
    steps = [say("about to ask"), _command_request(), {"wait_interrupt": True}, complete("interrupted")]
    server = ScriptedServer(tmp_path / "s", {"turns": [steps]})
    broker = AnsweringBroker(None)
    pool = server.pool()
    events: list[RuntimeEvent] = []

    async def drive() -> None:
        async for event in pool.run_turn("codex", "go", "chat-1", workspace=str(workspace), permissions=broker):
            events.append(event)

    try:
        task = asyncio.create_task(drive())
        async with asyncio.timeout(30):
            await broker.asked_event.wait()
        await pool.cancel("codex", "chat-1")
        await asyncio.wait_for(task, 20)
    finally:
        await pool.close_all()

    assert _error_code(events) is AcpErrorCode.CANCELLED
    assert broker.withdrawn
    assert server.answers() == [{"decision": "cancel"}]


async def test_a_stop_while_the_turn_is_being_started_ends_it_as_soon_as_it_exists(
    tmp_path: Path, workspace: Path
) -> None:
    scenario = {"turn_start_delay": 0.5, "turns": [[{"wait_interrupt": True}, complete("interrupted")]]}
    server = ScriptedServer(tmp_path / "s", scenario)
    pool = server.pool()
    events: list[RuntimeEvent] = []

    async def drive() -> None:
        async for event in pool.run_turn("codex", "go", "chat-1", workspace=str(workspace)):
            events.append(event)

    try:
        task = asyncio.create_task(drive())
        async with asyncio.timeout(30):
            while not server.received("turn/start"):
                await asyncio.sleep(0.01)
        await pool.cancel("codex", "chat-1")
        await asyncio.wait_for(task, 20)
    finally:
        await pool.close_all()

    assert _error_code(events) is AcpErrorCode.CANCELLED
    assert len(server.received("turn/interrupt")) == 1


async def test_a_stop_before_the_turn_exists_ends_it_as_soon_as_it_does(tmp_path: Path, workspace: Path) -> None:
    server = ScriptedServer(tmp_path / "s", {"turns": [[{"wait_interrupt": True}, complete("interrupted")]]})
    pool = server.pool()
    events: list[RuntimeEvent] = []

    async def drive() -> None:
        async for event in pool.run_turn("codex", "go", "chat-1", workspace=str(workspace)):
            events.append(event)

    try:
        task = asyncio.create_task(drive())
        async with asyncio.timeout(30):
            while not events:
                await asyncio.sleep(0.005)
        await pool.cancel("codex", "chat-1")
        await asyncio.wait_for(task, 20)
    finally:
        await pool.close_all()

    assert _error_code(events) is AcpErrorCode.CANCELLED
    assert RuntimeEventType.DONE not in [e.type for e in events]
