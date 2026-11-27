"""How a Claude Code turn reads and ends, against the real binary.

The model is scripted (``FakeAnthropicApi``): it says one sentence, asks for a ``Read`` of a file in the workspace, and
reports what it found. Everything else is real: the ``claude`` binary streams the messages, runs the tool, and stops at
the turn limit it is given, so a release that changes how the messages around a tool call are streamed, or how the CLI
says it ran out of turns, fails here and not in a user's chat. ``test_claude_transcripts.py`` replays recordings of
the same runs through the parser alone.

Run with ``-m integration``. Without the CLI the tests skip, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig, RuntimeEvent, RuntimeEventType, StopReason
from tests.toolkits.acp._fake_anthropic import FakeAnthropicApi
from tests.toolkits.acp.cli_contract import cli_binary

from ._fakes import errors_of, text_of, types_of

pytestmark = pytest.mark.integration

_BOOKKEEPING = (RuntimeEventType.STATUS_UPDATE, RuntimeEventType.USAGE_UPDATE)
_LEAD = "I will read the file."
_FINAL = "The file says hi."


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    (path / "note.txt").write_text("hi\n")
    return path


async def _turn(workspace: Path, home: Path, *, max_turns: int) -> list[RuntimeEvent]:
    binary = cli_binary("claude")
    (home / ".claude").mkdir(parents=True)
    async with FakeAnthropicApi(
        "Read", {"file_path": str(workspace / "note.txt")}, final_text=_FINAL, lead_text=_LEAD
    ) as api:
        config = RuntimeConfig(
            backend_type="cli",
            command=binary,
            auth_mode="api_key",
            env={
                "ANTHROPIC_API_KEY": "sk-ant-api03-fake",
                "ANTHROPIC_BASE_URL": api.base_url,
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            timeout_seconds=60,
            max_turns=max_turns,
        )
        runtime = CliRuntime("claude", config, workspace=str(workspace))
        return [event async for event in runtime.run_turn("read note.txt", "s1")]


async def test_what_claude_says_after_a_tool_call_is_set_apart_from_what_it_said_before(
    workspace: Path, tmp_path: Path
) -> None:
    events = await _turn(workspace, tmp_path / "home", max_turns=25)

    assert errors_of(events) == []
    assert text_of(events) == f"{_LEAD}\n\n{_FINAL}"
    assert [t for t in types_of(events) if t not in _BOOKKEEPING] == [
        RuntimeEventType.TEXT_DELTA,
        RuntimeEventType.TOOL_START,
        RuntimeEventType.TOOL_RESULT,
        RuntimeEventType.TEXT_DELTA,
        RuntimeEventType.DONE,
    ]
    assert events[-1].data["stop_reason"] is StopReason.END_TURN


async def test_a_turn_that_runs_out_of_turns_ends_as_a_stop_with_the_work_it_did(
    workspace: Path, tmp_path: Path
) -> None:
    events = await _turn(workspace, tmp_path / "home", max_turns=1)

    assert errors_of(events) == []
    assert text_of(events) == _LEAD
    assert RuntimeEventType.TOOL_RESULT in types_of(events)
    assert types_of(events)[-1] is RuntimeEventType.DONE
    assert events[-1].data["stop_reason"] is StopReason.MAX_TURN_REQUESTS
