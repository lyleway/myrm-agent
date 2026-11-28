"""Both ways into a delegated turn start the external CLI in the chat's workspace, and in no other directory.

The chain is real down to the subprocess: the stored project and chat, the converter that resolves them into the roots a
turn declares, ``ExternalAgentsMixin`` -> ``RuntimePool`` -> ``CliRuntime`` -> a stand-in ``claude`` that records the
directory it was started in, the prompt it was given and the session it was told to resume.
"""

from __future__ import annotations

import json
import stat
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from unittest.mock import AsyncMock, patch

import pytest

from app.ai_agents.general_agent.external_agents import ExternalAgentsMixin
from app.config.settings import get_settings
from app.database.models.chat import Chat
from app.platform_utils import get_session_factory
from app.services.agent.params.converter import convert_to_general_agent_params
from app.services.agent.params.models import AgentRequest
from app.services.agent.params.workspace_resolve import default_chat_workspace_path
from app.services.chat.chat_service import ChatService
from app.services.external_agents.runtime_pool_registry import get_chat_runtime_pool_registry
from app.services.project.project_service import ProjectService
from tests.api.agent.utils import get_model_selection

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in CLI is a POSIX shebang script")

_FAKE_CLAUDE = """\
#!__PYTHON__
import json, os, sys, uuid

here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.stdin.read()
resume = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else None
with open(os.path.join(here, "calls.jsonl"), "a") as handle:
    handle.write(json.dumps({"cwd": os.getcwd(), "prompt": prompt, "resume": resume}) + "\\n")
session_id = resume or str(uuid.uuid4())
print(json.dumps({"type": "system", "subtype": "init", "session_id": session_id}), flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}), flush=True)
print(json.dumps({"type": "result", "session_id": session_id}), flush=True)
"""

_TASK = "do it"

Entry = Callable[[ExternalAgentsMixin, list[object]], Awaitable[None]]


class AgentBuilder(Protocol):
    """Builds an agent whose only external agent is the stand-in; ``chat_id`` puts its pool in the chat's scope."""

    async def __call__(
        self, roots: tuple[str, ...], *, chat_id: str | None = None
    ) -> tuple[ExternalAgentsMixin, list[object]]: ...


@dataclass
class _FakeClaude:
    command: Path

    def calls(self) -> list[dict[str, str | None]]:
        log = self.command.parent / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def only_call(self) -> dict[str, str | None]:
        calls = self.calls()
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

    async def build(roots: tuple[str, ...], *, chat_id: str | None = None) -> tuple[ExternalAgentsMixin, list[object]]:
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
        mixin.chat_id = chat_id
        mixin._runtime_pool_scope_id = chat_id
        mixin._runtime_pool = None
        mixin._runtime_pool_from_registry = False
        mixin._owned_runtime_pool = None
        mixin.agent_id = "general"
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
    await get_chat_runtime_pool_registry().close_all()


async def _the_user_addresses_the_agent(mixin: ExternalAgentsMixin, _tools: list[object]) -> None:
    async for _ in mixin._direct_delegate_stream("claude", _TASK):
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
    assert (call["prompt"] or "").rstrip("\n") == _TASK


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


# Where a chat's workspace comes from: the project it belongs to, the folder the viewer picked for it, or the sandbox
# the server makes for it. These tests store the project and the chat as the product does and let the converter resolve
# them, so a source that stopped reaching the CLI would show in the directory the stand-in was started in.


async def _store_project(folder: Path | None) -> str:
    project_id = str((await ProjectService.create_project("workspace sources"))["id"])
    if folder is not None:
        await ProjectService.update_project(project_id, workspace_path=str(folder))
    return project_id


async def _store_chat(*, project_id: str | None = None, folder: Path | str | None = None) -> str:
    chat_id = f"workspace-sources-{uuid.uuid4().hex[:10]}"
    async with get_session_factory()() as db:
        db.add(
            Chat(
                id=chat_id,
                title="workspace sources",
                project_id=project_id,
                workspace_dir=None if folder is None else str(folder),
            )
        )
        await db.commit()
    return chat_id


async def _roots_declared_for(chat_id: str, *, picked: Path | None = None) -> tuple[str, ...]:
    """The roots the converter declares for a turn of the chat, ``picked`` being the folder its first turn carries."""
    from tests.api.agent.conftest import _build_mock_user_configs

    request = AgentRequest(
        message_id=f"msg-{uuid.uuid4().hex[:8]}",
        chat_id=chat_id,
        query="delegate it",
        model_selection=get_model_selection(),
        action_mode="agent",
        workspace_dir=None if picked is None else str(picked),
    )
    configs = AsyncMock(return_value=_build_mock_user_configs())
    with patch("app.core.channel_bridge.config_loader.load_user_configs", configs):
        params, *_ = await convert_to_general_agent_params(request, [])
    return params.declared_allowed_roots


async def _delegated_from(delegating_agent: AgentBuilder, fake_claude: _FakeClaude, roots: tuple[str, ...]) -> Path:
    """The one directory both entries started the CLI in; the test fails when they disagree."""
    mixin, tools = await delegating_agent(roots)
    for entry in sorted(_ENTRIES):
        await _ENTRIES[entry](mixin, tools)
    started_in = [call["cwd"] for call in fake_claude.calls()]
    assert len(started_in) == len(_ENTRIES), started_in
    assert len(set(started_in)) == 1, started_in
    return Path(str(started_in[0]))


@pytest.fixture
def server_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)


@pytest.mark.usefixtures("server_elsewhere")
async def test_the_project_folder_decides_where_a_chat_of_the_project_delegates_even_over_a_folder_the_chat_had_before(
    delegating_agent: AgentBuilder, fake_claude: _FakeClaude, tmp_path: Path
) -> None:
    project_folder, earlier_choice = tmp_path / "project", tmp_path / "earlier-choice"
    project_folder.mkdir()
    earlier_choice.mkdir()
    chat_id = await _store_chat(project_id=await _store_project(project_folder), folder=earlier_choice)

    roots = await _roots_declared_for(chat_id)

    assert await _delegated_from(delegating_agent, fake_claude, roots) == project_folder.resolve()


@pytest.mark.usefixtures("server_elsewhere")
async def test_the_folder_picked_for_a_chat_decides_where_it_delegates(
    delegating_agent: AgentBuilder, fake_claude: _FakeClaude, tmp_path: Path
) -> None:
    picked = tmp_path / "picked"
    picked.mkdir()
    chat_id = await _store_chat(folder=picked)

    roots = await _roots_declared_for(chat_id)

    assert await _delegated_from(delegating_agent, fake_claude, roots) == picked.resolve()


@pytest.mark.usefixtures("server_elsewhere")
async def test_a_project_with_no_folder_leaves_the_decision_to_the_folder_picked_for_the_chat(
    delegating_agent: AgentBuilder, fake_claude: _FakeClaude, tmp_path: Path
) -> None:
    picked = tmp_path / "picked"
    picked.mkdir()
    chat_id = await _store_chat(project_id=await _store_project(None), folder=picked)

    roots = await _roots_declared_for(chat_id)

    assert await _delegated_from(delegating_agent, fake_claude, roots) == picked.resolve()


@pytest.mark.usefixtures("server_elsewhere")
async def test_a_chat_with_no_folder_at_all_delegates_in_its_own_sandbox(
    delegating_agent: AgentBuilder, fake_claude: _FakeClaude
) -> None:
    chat_id = await _store_chat()

    roots = await _roots_declared_for(chat_id)

    sandbox = default_chat_workspace_path(chat_id)
    assert await _delegated_from(delegating_agent, fake_claude, roots) == sandbox.resolve()
    assert sandbox.is_dir()


@pytest.mark.usefixtures("server_elsewhere")
async def test_a_folder_named_by_the_first_turn_of_a_chat_that_does_not_exist_yet_is_where_it_delegates(
    delegating_agent: AgentBuilder, fake_claude: _FakeClaude, tmp_path: Path
) -> None:
    picked = tmp_path / "picked-before-the-chat-existed"
    picked.mkdir()

    roots = await _roots_declared_for(f"not-stored-yet-{uuid.uuid4().hex[:10]}", picked=picked)

    assert await _delegated_from(delegating_agent, fake_claude, roots) == picked.resolve()


@pytest.mark.usefixtures("server_elsewhere")
async def test_a_stored_folder_that_covers_protected_locations_is_not_where_a_chat_delegates(
    delegating_agent: AgentBuilder, fake_claude: _FakeClaude
) -> None:
    # The folder holding the application's state is one the sandbox refuses to be handed, whoever stored it.
    too_broad = Path(get_settings().database.state_dir).parent
    chat_id = await _store_chat(folder=too_broad)

    roots = await _roots_declared_for(chat_id)

    started_in = await _delegated_from(delegating_agent, fake_claude, roots)
    assert started_in == default_chat_workspace_path(chat_id).resolve()
    assert started_in != too_broad.resolve()


@pytest.mark.usefixtures("server_elsewhere")
async def test_a_folder_picked_in_the_middle_of_a_chat_is_where_its_next_turn_starts_and_the_old_session_is_left_alone(
    delegating_agent: AgentBuilder, fake_claude: _FakeClaude, tmp_path: Path
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    chat_id = await _store_chat(folder=first)

    async def a_turn_of_the_chat() -> None:
        mixin, tools = await delegating_agent(await _roots_declared_for(chat_id), chat_id=chat_id)
        await _the_user_addresses_the_agent(mixin, tools)

    await a_turn_of_the_chat()
    await a_turn_of_the_chat()
    await ChatService.update_chat_fields(chat_id, {"workspace_dir": str(second)})
    await a_turn_of_the_chat()

    turns = fake_claude.calls()
    assert [turn["cwd"] for turn in turns] == [str(first.resolve())] * 2 + [str(second.resolve())]
    # The second turn continued the first one's session; the one in the new folder cannot (Claude resumes only in the
    # directory that created a session), so it starts a session of its own.
    assert turns[0]["resume"] is None
    assert turns[1]["resume"] is not None
    assert turns[2]["resume"] is None
