"""A real ``codex app-server``, driven the way the server drives it, asks the host and does what the host answers.

The model is scripted (``FakeResponsesApi``); everything else is real: the binary, the protocol it speaks, the approval
requests it sends for an edit or a command, what it does with the answer (run the command, write the patch, or tell the
model it was refused), and how a turn is stopped. A Codex release that changes any of those fails here and not in a
user's chat.

Run with ``-m integration``. A Codex that is not installed skips the tests, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names
it. No login and no real model are involved.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import cast

import pytest

from myrm_agent_harness.toolkits.acp.runtime.codex.protocol import MINIMUM_SERVER_VERSION
from myrm_agent_harness.toolkits.acp.types import PermissionMode, RuntimeEvent, RuntimeEventType
from tests.toolkits.acp._brokers import AnsweringBroker
from tests.toolkits.acp._fake_model_apis import FakeResponsesApi, ToolCall
from tests.toolkits.acp.cli_contract import cli_binary
from tests.toolkits.acp.runtime.codex._real import (
    codex_pool,
    ended_cleanly,
    failed_tool_results,
    failure_of,
    open_connection,
    run_turn,
    text_of,
    types_of,
    write_codex_home,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="the scripted shell command is POSIX"),
]

# Codex takes a patch from the shell as an edit of its own: it announces a file change and asks about it as one.
_PATCH = "apply_patch <<'EOF'\n*** Begin Patch\n*** Add File: made.txt\n+hello\n*** End Patch\nEOF"
_REASON = "write the marker file"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def _command(command: str) -> ToolCall:
    """A command that only runs inside the sandbox."""
    return ToolCall("exec_command", {"cmd": command})


def _escalated(command: str) -> ToolCall:
    """A command the agent asks to run outside the sandbox, the way it does once the sandbox has refused it."""
    return ToolCall(
        "exec_command", {"cmd": command, "sandbox_permissions": "require_escalated", "justification": _REASON}
    )


def _patch() -> ToolCall:
    return ToolCall("exec_command", {"cmd": _PATCH})


async def _turn(
    tmp_path: Path,
    workspace: Path,
    calls: list[ToolCall],
    *,
    mode: PermissionMode = "safe",
    broker: AnsweringBroker | None = None,
    final_text: str = "finished",
    config: str = "",
) -> tuple[list[RuntimeEvent], FakeResponsesApi]:
    """One turn of ``calls`` against a real server; ``config`` is what the user's own ``config.toml`` also says."""
    async with FakeResponsesApi(calls=calls, final_text=final_text) as api:
        env = write_codex_home(tmp_path, api.base_url)
        with (Path(env["CODEX_HOME"]) / "config.toml").open("a") as user_config:
            user_config.write(config)
        pool = codex_pool(env, mode=mode)
        try:
            events = await run_turn(pool, workspace, broker=broker)
        finally:
            await pool.close_all()
    return events, api


async def test_a_turn_streams_its_text_and_reports_its_usage_once(tmp_path: Path, workspace: Path) -> None:
    events, _ = await _turn(tmp_path, workspace, [], final_text="the answer is 42")

    assert ended_cleanly(events)
    assert text_of(events) == "the answer is 42"
    assert types_of(events)[0] is RuntimeEventType.STATUS_UPDATE
    usage = [e.data for e in events if e.type is RuntimeEventType.USAGE_UPDATE]
    assert sum(cast("int", u["input_tokens"]) for u in usage) == 10
    assert sum(cast("int", u["output_tokens"]) for u in usage) == 5
    done = events[-1]
    assert done.data["stop_reason"] == "end_turn"


async def test_a_command_beyond_the_sandbox_asks_as_an_execution_and_runs_once_allowed(
    tmp_path: Path, workspace: Path
) -> None:
    broker = AnsweringBroker(True)

    events, _ = await _turn(tmp_path, workspace, [_escalated("echo ran > ran.txt")], broker=broker)

    assert ended_cleanly(events)
    assert (workspace / "ran.txt").read_text().strip() == "ran"
    (request,) = broker.asked
    assert (request.agent, request.session_id, request.kind) == ("codex", "chat-1", "execute")
    assert "echo ran > ran.txt" in request.title
    assert "echo ran > ran.txt" in str(request.input["command"])
    assert request.input["reason"] == _REASON
    assert failed_tool_results(events) == []


async def test_a_refused_command_does_not_run_and_the_turn_still_ends(tmp_path: Path, workspace: Path) -> None:
    broker = AnsweringBroker(False)

    events, api = await _turn(tmp_path, workspace, [_escalated("echo ran > ran.txt")], broker=broker)

    assert ended_cleanly(events)
    assert not (workspace / "ran.txt").exists()
    assert len(broker.asked) == 1
    assert len(failed_tool_results(events)) == 1
    assert len(api.tool_results) == 1


async def test_a_patch_asks_as_an_edit_with_its_paths_and_applies_once_allowed(tmp_path: Path, workspace: Path) -> None:
    broker = AnsweringBroker(True)

    events, _ = await _turn(tmp_path, workspace, [_patch()], broker=broker)

    assert ended_cleanly(events)
    assert (workspace / "made.txt").read_text() == "hello\n"
    (request,) = broker.asked
    assert request.kind == "edit"
    assert request.tool_name == "file_change"
    assert any(path.endswith("made.txt") for path in request.paths)
    assert "hello" in str(request.input["changes"])


async def test_a_refused_patch_writes_nothing(tmp_path: Path, workspace: Path) -> None:
    broker = AnsweringBroker(False)

    events, _ = await _turn(tmp_path, workspace, [_patch()], broker=broker)

    assert ended_cleanly(events)
    assert not (workspace / "made.txt").exists()
    assert len(broker.asked) == 1


async def test_with_nobody_to_ask_a_safe_agent_cannot_write(tmp_path: Path, workspace: Path) -> None:
    events, api = await _turn(tmp_path, workspace, [_escalated("echo ran > ran.txt")])

    assert ended_cleanly(events)
    assert not (workspace / "ran.txt").exists()
    assert len(api.tool_results) == 1
    assert "approval policy is Never" in api.tool_results[0]


async def test_a_command_that_writes_without_asking_for_more_fails_in_the_sandbox(
    tmp_path: Path, workspace: Path
) -> None:
    broker = AnsweringBroker(True)

    events, api = await _turn(tmp_path, workspace, [_command("echo ran > ran.txt")], broker=broker)

    assert ended_cleanly(events)
    assert not (workspace / "ran.txt").exists()
    assert broker.asked == []
    assert "code 1" in api.tool_results[0]


async def test_allow_all_acts_inside_the_workspace_without_asking_anyone(tmp_path: Path, workspace: Path) -> None:
    broker = AnsweringBroker(False)

    events, _ = await _turn(tmp_path, workspace, [_command("echo ran > ran.txt")], mode="allow_all", broker=broker)

    assert ended_cleanly(events)
    assert (workspace / "ran.txt").read_text().strip() == "ran"
    assert broker.asked == []


async def test_allow_all_keeps_the_sandbox_settings_the_user_wrote_in_the_codex_config(
    tmp_path: Path, workspace: Path
) -> None:
    granted, other = tmp_path / "granted", tmp_path / "other"
    granted.mkdir()
    other.mkdir()
    # The temp directories are writable by default; excluding them leaves only the workspace and the granted root.
    config = (
        f'\n[sandbox_workspace_write]\nwritable_roots = ["{granted}"]\n'
        "exclude_tmpdir_env_var = true\nexclude_slash_tmp = true\n"
    )
    calls = [_command(f"echo yes > {granted}/in.txt"), _command(f"echo no > {other}/out.txt")]

    events, _ = await _turn(tmp_path, workspace, calls, mode="allow_all", config=config)

    assert ended_cleanly(events)
    assert (granted / "in.txt").read_text().strip() == "yes"
    assert not (other / "out.txt").exists()


async def test_a_command_that_only_reads_runs_in_the_sandbox_and_asks_nobody(tmp_path: Path, workspace: Path) -> None:
    (workspace / "existing.txt").write_text("alpha beta\n")
    broker = AnsweringBroker(False)

    events, api = await _turn(tmp_path, workspace, [_command("cat existing.txt")], broker=broker)

    assert ended_cleanly(events)
    assert broker.asked == []
    assert "alpha beta" in api.tool_results[0]


async def test_a_stop_while_a_request_waits_withdraws_it_and_ends_the_turn(tmp_path: Path, workspace: Path) -> None:
    broker = AnsweringBroker(None)
    async with FakeResponsesApi(calls=[_escalated("echo ran > ran.txt")]) as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url))
        try:
            turn = asyncio.create_task(run_turn(pool, workspace, broker=broker))
            async with asyncio.timeout(60):
                await broker.asked_event.wait()
            await pool.cancel("codex", "chat-1")
            events = await asyncio.wait_for(turn, 20)
        finally:
            await pool.close_all()

    assert broker.withdrawn
    assert not (workspace / "ran.txt").exists()
    (error,) = [e for e in events if e.type is RuntimeEventType.ERROR]
    assert failure_of(error).code.value == "cancelled"


async def test_a_stop_ends_the_turn_as_cancelled_and_the_server_serves_the_next_one(
    tmp_path: Path, workspace: Path
) -> None:
    async with FakeResponsesApi(hang=True) as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url))
        try:
            turn = asyncio.create_task(run_turn(pool, workspace))
            async with asyncio.timeout(60):
                while not api.requests:
                    await asyncio.sleep(0.05)
            await pool.cancel("codex", "chat-1")
            events = await asyncio.wait_for(turn, 30)

            api.hang = False
            api.release()
            second = await run_turn(pool, workspace, prompt="again")
        finally:
            api.release()
            await pool.close_all()

    (error,) = [e for e in events if e.type is RuntimeEventType.ERROR]
    assert failure_of(error).code.value == "cancelled"
    assert ended_cleanly(second)


async def test_the_server_says_its_version_the_way_the_host_reads_it(tmp_path: Path, workspace: Path) -> None:
    """The host refuses a server older than it speaks to by the version in ``initialize``; a Codex that words it
    differently would be let through whatever its version, so that is a failure here."""
    connection = await open_connection(cli_binary("codex"), write_codex_home(tmp_path, "http://127.0.0.1:9"), workspace)
    try:
        assert connection.version is not None
        assert connection.version >= MINIMUM_SERVER_VERSION
    finally:
        await connection.close(graceful=True)
