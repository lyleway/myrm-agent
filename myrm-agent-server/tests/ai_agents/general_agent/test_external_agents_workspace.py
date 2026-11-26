"""Both ways into a delegated turn start the external CLI in the chat's workspace, and in no other directory.

The chain is real down to the subprocess: ``ExternalAgentsMixin`` -> ``RuntimePool`` -> ``CliRuntime`` -> a stand-in
``claude`` that records the directory it was started in and the prompt it was given.
"""

from __future__ import annotations

import json
import stat
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.ai_agents.general_agent.external_agents import ExternalAgentsMixin

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in CLI is a POSIX shebang script")

_FAKE_CLAUDE = """\
#!__PYTHON__
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.stdin.read()
with open(os.path.join(here, "calls.jsonl"), "a") as handle:
    handle.write(json.dumps({"cwd": os.getcwd(), "prompt": prompt}) + "\\n")
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}), flush=True)
print(json.dumps({"type": "result", "session_id": "s"}), flush=True)
"""

_TASK = "do it"

Entry = Callable[[ExternalAgentsMixin, list[object]], Awaitable[None]]
AgentBuilder = Callable[[tuple[str, ...]], Awaitable[tuple[ExternalAgentsMixin, list[object]]]]


@dataclass
class _FakeClaude:
    command: Path

    def only_call(self) -> dict[str, str]:
        log = self.command.parent / "calls.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        assert len(calls) == 1, calls
        return calls[0]


@pytest.fixture
def fake_claude(tmp_path: Path) -> _FakeClaude:
    command = tmp_path / "bin" / "claude"
    command.parent.mkdir()
    command.write_text(_FAKE_CLAUDE.replace("__PYTHON__", sys.executable))
    command.chmod(command.stat().st_mode | stat.S_IEXEC)
    return _FakeClaude(command)


@pytest.fixture
async def delegating_agent(fake_claude: _FakeClaude) -> AsyncIterator[AgentBuilder]:
    """A factory of agents whose only external agent is the stand-in; every pool it built is closed afterwards."""
    agents: list[ExternalAgentsMixin] = []

    async def build(roots: tuple[str, ...]) -> tuple[ExternalAgentsMixin, list[object]]:
        mixin = ExternalAgentsMixin.__new__(ExternalAgentsMixin)
        mixin.external_agents_config = [
            {
                "name": "claude",
                "type": "cli",
                "command": str(fake_claude.command),
                "args": ["-p", "--output-format", "stream-json"],
                "permissionMode": "safe",
                "timeout": 20,
            }
        ]
        mixin.chat_id = None
        mixin._runtime_pool_scope_id = None
        mixin._runtime_pool = None
        mixin._runtime_pool_from_registry = False
        mixin._owned_runtime_pool = None
        mixin.agent_id = "general"
        mixin.force_external_agent = None
        mixin.locale = "en"
        mixin.quote = None
        mixin.declared_allowed_roots = roots
        tools: list[object] = []
        await mixin._do_setup_external_agents(tools, mount_invoke_acp_agent_tool=True)
        agents.append(mixin)
        return mixin, tools

    yield build
    for mixin in agents:
        if mixin._owned_runtime_pool is not None:
            await mixin._owned_runtime_pool.close_all()


async def _the_user_addresses_the_agent(mixin: ExternalAgentsMixin, _tools: list[object]) -> None:
    async for _ in mixin._direct_delegate_stream("claude", _TASK, chat_id="chat-1"):
        pass


async def _the_model_calls_the_tool(_mixin: ExternalAgentsMixin, tools: list[object]) -> None:
    tool = next(t for t in tools if getattr(t, "name", None) == "invoke_acp_agent_tool")
    await tool.ainvoke({"agent_name": "claude", "task": _TASK, "mode": "persistent"})  # type: ignore[attr-defined]


_ENTRIES: dict[str, Entry] = {"direct": _the_user_addresses_the_agent, "tool": _the_model_calls_the_tool}


@pytest.mark.parametrize("entry", sorted(_ENTRIES))
async def test_both_entries_start_the_cli_in_the_chats_workspace_and_hand_it_the_task_as_written(
    entry: str,
    delegating_agent: AgentBuilder,
    fake_claude: _FakeClaude,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_workspace = tmp_path / "chat-workspace"
    chat_workspace.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # Wherever the server process happens to be must not decide where the agent works.
    monkeypatch.chdir(elsewhere)
    mixin, tools = await delegating_agent((str(chat_workspace),))

    await _ENTRIES[entry](mixin, tools)

    call = fake_claude.only_call()
    assert call["cwd"] == str(chat_workspace.resolve())
    # The CLI reads its prompt from stdin, which ends the line.
    assert call["prompt"].rstrip("\n") == _TASK


@pytest.mark.parametrize("entry", sorted(_ENTRIES))
async def test_a_chat_with_no_declared_workspace_delegates_in_the_process_workspace(
    entry: str,
    delegating_agent: AgentBuilder,
    fake_claude: _FakeClaude,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_workspace = tmp_path / "process-workspace"
    process_workspace.mkdir()
    monkeypatch.chdir(process_workspace)
    mixin, tools = await delegating_agent(())

    await _ENTRIES[entry](mixin, tools)

    assert fake_claude.only_call()["cwd"] == str(process_workspace.resolve())
