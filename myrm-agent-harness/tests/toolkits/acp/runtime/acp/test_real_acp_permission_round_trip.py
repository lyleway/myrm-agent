"""A real Gemini or Qwen, driven over ACP the way the server drives it, asks the host and does what the host answers.

The model is scripted (``FakeGeminiApi``, ``FakeOpenAiChatApi``); everything else is real: the binary, the contract
that puts it on ACP and starts it asking, the permission request it sends, the answer the host's policy gives, and what
the agent then does with it (write the file, run the command, or tell the model it was refused). A release that
changes any of those fails here and not in a user's chat.

Run with ``-m integration``. An agent that is not installed skips its tests, unless ``MYRM_CONTRACT_REQUIRE_CLIS``
names it. No login and no real model are involved.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    PermissionMode,
    PermissionRequest,
    PermissionVerdict,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
)
from tests.toolkits.acp._fake_model_apis import FakeGeminiApi, FakeOpenAiChatApi, ScriptedModelApi, ToolCall
from tests.toolkits.acp.cli_contract import cli_binary

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="the scripted shell command is POSIX"),
]

_TURN_SECONDS = 90
_PROMPT = "do the scripted work"


@dataclass(frozen=True)
class _Agent:
    """How to point one agent at its scripted model, and the names its tools go by."""

    api: Callable[[Sequence[ToolCall]], ScriptedModelApi]
    env: Callable[[str], dict[str, str]]
    edit_tool: str
    # What the edit tool takes besides the file and the two strings.
    edit_extra: Mapping[str, object] = field(default_factory=dict)


_AGENTS = {
    "gemini": _Agent(
        api=lambda calls: FakeGeminiApi(calls=calls),
        env=lambda url: {
            "GEMINI_API_KEY": "fake-key",
            "GOOGLE_GEMINI_BASE_URL": url,
            "GEMINI_MODEL": "gemini-2.5-flash",
        },
        edit_tool="replace",
        edit_extra={"instruction": "Rename alpha to omega"},
    ),
    "qwen": _Agent(
        api=lambda calls: FakeOpenAiChatApi(calls=calls),
        env=lambda url: {"OPENAI_API_KEY": "fake-key", "OPENAI_BASE_URL": f"{url}/v1", "OPENAI_MODEL": "fake-model"},
        edit_tool="edit",
    ),
}


class _Broker:
    """The host: answers every request with ``allowed`` (``None``: never answers) and keeps what it was asked."""

    def __init__(self, allowed: bool | None) -> None:
        self._allowed = allowed
        self.asked: list[PermissionRequest] = []
        self.asked_event = asyncio.Event()
        self.withdrawn = False

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked.append(request)
        self.asked_event.set()
        if self._allowed is None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.withdrawn = True
                raise
        return PermissionVerdict(allowed=bool(self._allowed))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def _pool(name: str, api: ScriptedModelApi, home: Path, mode: PermissionMode) -> RuntimePool:
    """A pool with the agent registered the way every config stored before the move to ACP says: as a CLI."""
    pool = RuntimePool(max_concurrent=1)
    pool.register(
        name,
        RuntimeConfig(
            backend_type="cli",
            command=cli_binary(name),
            auth_mode="api_key",
            env={**_AGENTS[name].env(api.base_url), "HOME": str(home)},
            timeout_seconds=_TURN_SECONDS,
            permission_mode=mode,
        ),
    )
    return pool


async def _turn(
    name: str,
    workspace: Path,
    home: Path,
    calls: Sequence[ToolCall],
    *,
    mode: PermissionMode = "safe",
    broker: _Broker | None = None,
) -> list[RuntimeEvent]:
    async with _AGENTS[name].api(calls) as api:
        pool = _pool(name, api, home, mode)
        events: list[RuntimeEvent] = []
        try:
            async for event in pool.run_turn(name, _PROMPT, "chat-1", workspace=str(workspace), permissions=broker):
                events.append(event)
        finally:
            await pool.close_all()
    return events


def _write(workspace: Path) -> ToolCall:
    return ToolCall("write_file", {"file_path": str(workspace / "out.txt"), "content": "hello"})


def _shell() -> ToolCall:
    return ToolCall("run_shell_command", {"command": "echo ran > ran.txt", "description": "Write the marker file"})


def _types(events: list[RuntimeEvent]) -> list[RuntimeEventType]:
    return [event.type for event in events]


def _failed_tool_results(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
    return [e for e in events if e.type is RuntimeEventType.TOOL_RESULT and e.data["is_error"]]


def _ended_cleanly(events: list[RuntimeEvent]) -> bool:
    return RuntimeEventType.ERROR not in _types(events) and _types(events)[-1] is RuntimeEventType.DONE


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_a_new_file_is_created_once_the_host_allows_it(agent: str, workspace: Path, home: Path) -> None:
    broker = _Broker(True)

    events = await _turn(agent, workspace, home, [_write(workspace)], broker=broker)

    assert _ended_cleanly(events)
    assert (workspace / "out.txt").read_text() == "hello"
    assert _failed_tool_results(events) == []
    (request,) = broker.asked
    assert (request.agent, request.session_id, request.kind) == (agent, "chat-1", "edit")
    assert str((workspace / "out.txt").resolve()) in request.paths


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_a_refused_write_makes_no_file_and_the_turn_still_ends(agent: str, workspace: Path, home: Path) -> None:
    broker = _Broker(False)

    events = await _turn(agent, workspace, home, [_write(workspace)], broker=broker)

    assert _ended_cleanly(events)
    assert not (workspace / "out.txt").exists()
    assert len(broker.asked) == 1
    assert len(_failed_tool_results(events)) == 1


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_allow_all_writes_without_asking_anyone(agent: str, workspace: Path, home: Path) -> None:
    events = await _turn(agent, workspace, home, [_write(workspace)], mode="allow_all")

    assert _ended_cleanly(events)
    assert (workspace / "out.txt").read_text() == "hello"


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_with_nobody_to_ask_a_safe_agent_cannot_write(agent: str, workspace: Path, home: Path) -> None:
    events = await _turn(agent, workspace, home, [_write(workspace)])

    assert _ended_cleanly(events)
    assert not (workspace / "out.txt").exists()
    assert len(_failed_tool_results(events)) == 1


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_a_shell_command_asks_as_an_execution_and_runs_once_allowed(
    agent: str, workspace: Path, home: Path
) -> None:
    broker = _Broker(True)

    events = await _turn(agent, workspace, home, [_shell()], broker=broker)

    assert _ended_cleanly(events)
    assert (workspace / "ran.txt").read_text().strip() == "ran"
    (request,) = broker.asked
    assert request.kind == "execute"
    assert "echo ran > ran.txt" in f"{request.title} {request.input}"


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_a_refused_shell_command_does_not_run(agent: str, workspace: Path, home: Path) -> None:
    broker = _Broker(False)

    events = await _turn(agent, workspace, home, [_shell()], broker=broker)

    assert _ended_cleanly(events)
    assert not (workspace / "ran.txt").exists()
    assert len(broker.asked) == 1


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_reading_a_file_asks_nobody(agent: str, workspace: Path, home: Path) -> None:
    (workspace / "existing.txt").write_text("alpha beta\n")
    broker = _Broker(False)
    read = ToolCall("read_file", {"file_path": str(workspace / "existing.txt")})

    events = await _turn(agent, workspace, home, [read], broker=broker)

    assert _ended_cleanly(events)
    assert broker.asked == []
    assert _failed_tool_results(events) == []


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_an_existing_file_is_edited_once_the_host_allows_it(agent: str, workspace: Path, home: Path) -> None:
    target = workspace / "existing.txt"
    target.write_text("alpha beta\n")
    spec = _AGENTS[agent]
    edit = ToolCall(
        spec.edit_tool,
        {"file_path": str(target), "old_string": "alpha", "new_string": "omega", **spec.edit_extra},
    )
    broker = _Broker(True)

    events = await _turn(
        agent, workspace, home, [ToolCall("read_file", {"file_path": str(target)}), edit], broker=broker
    )

    assert _ended_cleanly(events)
    assert target.read_text() == "omega beta\n"
    (request,) = broker.asked
    assert request.kind == "edit"


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_stopping_a_turn_while_the_host_is_asked_ends_it_without_the_write(
    agent: str, workspace: Path, home: Path
) -> None:
    broker = _Broker(None)

    async with _AGENTS[agent].api([_write(workspace)]) as api:
        pool = _pool(agent, api, home, "safe")
        events: list[RuntimeEvent] = []

        async def run() -> None:
            async for event in pool.run_turn(agent, _PROMPT, "chat-1", workspace=str(workspace), permissions=broker):
                events.append(event)

        try:
            turn = asyncio.create_task(run())
            await asyncio.wait_for(broker.asked_event.wait(), timeout=_TURN_SECONDS)
            await pool.cancel(agent, "chat-1")
            await asyncio.wait_for(turn, timeout=60)
        finally:
            await pool.close_all()

    assert broker.withdrawn
    assert not (workspace / "out.txt").exists()
    assert _ended_cleanly(events)
    assert [e.data["stop_reason"] for e in events if e.type is RuntimeEventType.DONE] == ["cancelled"]
