"""Claude Code's permission prompt against the real binary: what the host answers is what the CLI does.

The model is scripted (``FakeAnthropicApi``): it asks for one Bash call and then reports. Everything else is real: the
``claude`` binary decides that the call needs permission, asks the loopback endpoint of ``PermissionBridge`` through
``--permission-prompt-tool``, and acts on the verdict, so a release that changes the prompt's contract (the tool
arguments, the reply shape, how a refusal reaches the model) fails here and not in a user's chat.

Run with ``-m integration``. Without the CLI the tests skip, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime
from myrm_agent_harness.toolkits.acp.types import (
    AcpErrorCode,
    PermissionRequest,
    PermissionVerdict,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
)
from tests.toolkits.acp._fake_anthropic import FakeAnthropicApi
from tests.toolkits.acp.cli_contract import cli_binary

from ._fakes import error_codes_of, errors_of, text_of, types_of

pytestmark = pytest.mark.integration

_MARKER = "permission-prompt-ran"
_COMMAND = f"echo {_MARKER} > ran.txt"
_TOOL_INPUT = {"command": _COMMAND, "description": "Write the marker file"}
_TURN_SECONDS = 60


class _Broker:
    """Answers every request with ``verdict`` after ``delay`` seconds (``None`` never answers), and records the asks."""

    def __init__(self, verdict: PermissionVerdict | None, delay: float = 0.0) -> None:
        self._verdict = verdict
        self._delay = delay
        self.asked: list[PermissionRequest] = []
        self.asked_event = asyncio.Event()
        self.cancelled = False

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked.append(request)
        self.asked_event.set()
        try:
            if self._verdict is None:
                await asyncio.Event().wait()
            await asyncio.sleep(self._delay)
            assert self._verdict is not None
            return self._verdict
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _runtime(binary: str, api: FakeAnthropicApi, home: Path, workspace: Path) -> CliRuntime:
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
        timeout_seconds=_TURN_SECONDS,
        max_turns=3,
    )
    return CliRuntime("claude", config, workspace=str(workspace))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    (path / ".claude").mkdir(parents=True)
    return path


async def _turn(runtime: CliRuntime, broker: _Broker | None) -> list[RuntimeEvent]:
    return [event async for event in runtime.run_turn("run the marker command", "s1", permissions=broker)]


def _only_tool_result(api: FakeAnthropicApi) -> dict[str, object]:
    (result,) = api.tool_results
    return result


async def test_an_allowed_request_lets_claude_run_the_command(workspace: Path, home: Path) -> None:
    binary = cli_binary("claude")
    broker = _Broker(PermissionVerdict(allowed=True))

    async with FakeAnthropicApi("Bash", _TOOL_INPUT) as api:
        events = await _turn(_runtime(binary, api, home, workspace), broker)

    assert errors_of(events) == []
    assert types_of(events)[-1] is RuntimeEventType.DONE
    assert text_of(events) == "finished"
    (request,) = broker.asked
    assert (request.agent, request.session_id, request.tool_name, request.kind) == ("claude", "s1", "Bash", "execute")
    assert request.input["command"] == _COMMAND
    assert request.tool_call_id is not None
    assert (workspace / "ran.txt").read_text().strip() == _MARKER
    assert not _only_tool_result(api).get("is_error")


@pytest.mark.timeout(420)
async def test_a_person_taking_minutes_is_still_waited_for(workspace: Path, home: Path) -> None:
    # Without a timeout in the bridge's server entry Claude refuses a prompt by itself after 60 seconds, and after 300
    # seconds without a reply even with a long call timeout. This one answers after the longer of the two, so the test
    # takes five minutes; the turn's own time limit (shorter still) stands still while the person decides.
    binary = cli_binary("claude")
    broker = _Broker(PermissionVerdict(allowed=True), delay=310.0)

    async with FakeAnthropicApi("Bash", _TOOL_INPUT) as api:
        events = await _turn(_runtime(binary, api, home, workspace), broker)

    assert errors_of(events) == []
    assert (workspace / "ran.txt").read_text().strip() == _MARKER
    assert not _only_tool_result(api).get("is_error")


async def test_a_refused_request_keeps_the_command_from_running_and_tells_the_model_why(
    workspace: Path, home: Path
) -> None:
    binary = cli_binary("claude")
    broker = _Broker(PermissionVerdict(allowed=False, message="Not on my watch."))

    async with FakeAnthropicApi("Bash", _TOOL_INPUT) as api:
        events = await _turn(_runtime(binary, api, home, workspace), broker)

    assert errors_of(events) == []
    assert types_of(events)[-1] is RuntimeEventType.DONE
    assert len(broker.asked) == 1
    assert not (workspace / "ran.txt").exists()
    result = _only_tool_result(api)
    assert result.get("is_error") is True
    assert "Not on my watch." in str(result.get("content"))


async def test_with_no_host_to_ask_claude_refuses_by_itself(workspace: Path, home: Path) -> None:
    binary = cli_binary("claude")

    async with FakeAnthropicApi("Bash", _TOOL_INPUT) as api:
        events = await _turn(_runtime(binary, api, home, workspace), None)

    assert errors_of(events) == []
    assert not (workspace / "ran.txt").exists()
    assert _only_tool_result(api).get("is_error") is True


async def test_stopping_a_turn_while_a_request_waits_ends_it_without_running_the_command(
    workspace: Path, home: Path
) -> None:
    binary = cli_binary("claude")
    broker = _Broker(None)

    async with FakeAnthropicApi("Bash", _TOOL_INPUT) as api:
        runtime = _runtime(binary, api, home, workspace)
        turn = asyncio.create_task(_turn(runtime, broker))
        await asyncio.wait_for(broker.asked_event.wait(), timeout=_TURN_SECONDS)
        await runtime.cancel("s1")
        events = await asyncio.wait_for(turn, timeout=60)

    assert broker.cancelled
    assert not (workspace / "ran.txt").exists()
    assert error_codes_of(events) == [AcpErrorCode.CANCELLED]
