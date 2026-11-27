"""``CliRuntime`` turns in which Claude puts its permission prompts to the host, against the scripted CLI stand-in.

The stand-in asks the way Claude Code does: it reads ``--mcp-config``, connects to the MCP endpoint named there
over HTTP and calls the ``--permission-prompt-tool`` tool, so what is exercised is the runtime's wiring — flags,
endpoint lifetime, the broker behind it — and the endpoint itself.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli import permission_bridge
from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime
from myrm_agent_harness.toolkits.acp.types import (
    PermissionRequest,
    PermissionVerdict,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
)

from ._fakes import FakeCli, errors_of, make_runtime, text_of, types_of

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the CLI stand-ins are POSIX shebang scripts")

_BASH = {"tool_name": "Bash", "input": {"command": "ls", "description": "List files"}}
_EDIT = {"tool_name": "Edit", "input": {"file_path": "/work/a.py", "old_string": "a", "new_string": "b"}}


class _Broker:
    """Answers the n-th request with the n-th verdict (the last one repeats), after ``delay`` seconds."""

    def __init__(self, *verdicts: PermissionVerdict, delay: float = 0.0) -> None:
        self._verdicts = list(verdicts) or [PermissionVerdict(allowed=True)]
        self._delay = delay
        self.asked: list[PermissionRequest] = []
        self.asked_event = asyncio.Event()
        self.cancelled = False

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked.append(request)
        self.asked_event.set()
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self._verdicts[min(len(self.asked) - 1, len(self._verdicts) - 1)]


def _argv_of(call: Mapping[str, object]) -> list[str]:
    argv = call["argv"]
    assert isinstance(argv, list)
    return argv


def _argv(fake_cli: FakeCli) -> list[str]:
    (call,) = fake_cli.calls()
    return _argv_of(call)


async def _turn(runtime: CliRuntime, broker: _Broker | None) -> list[RuntimeEvent]:
    return [event async for event in runtime.run_turn("hello", "s1", permissions=broker)]


class TestClaudeAsksTheHost:
    async def test_a_safe_turn_hands_claudes_permission_prompts_to_the_host_one_by_one(self, fake_cli: FakeCli) -> None:
        fake_cli.set_scenario({"asks": [_BASH, _EDIT]})
        broker = _Broker(PermissionVerdict(allowed=True), PermissionVerdict(allowed=False, message="Not that file."))

        events = await _turn(make_runtime(fake_cli), broker)

        assert text_of(events) == "allow; deny"
        assert types_of(events)[-1] is RuntimeEventType.DONE
        assert [(r.agent, r.session_id, r.tool_name, r.kind) for r in broker.asked] == [
            ("claude", "s1", "Bash", "execute"),
            ("claude", "s1", "Edit", "edit"),
        ]
        answers = json.loads((fake_cli.bin_dir / "answers.json").read_text())
        assert answers == [
            {"behavior": "allow", "updatedInput": _BASH["input"]},
            {"behavior": "deny", "message": "Not that file."},
        ]

    async def test_claude_is_launched_to_ask_in_its_default_mode_and_the_bridge_is_gone_after_the_turn(
        self, fake_cli: FakeCli
    ) -> None:
        fake_cli.set_scenario({"asks": [_BASH]})

        await _turn(make_runtime(fake_cli), _Broker())

        argv = _argv(fake_cli)
        assert argv[argv.index("--permission-mode") + 1] == "default"
        assert argv[argv.index("--permission-prompt-tool") + 1] == "mcp__myrm_permissions__approve"
        config = Path(argv[argv.index("--mcp-config") + 1])
        assert not config.exists()

    async def test_without_a_broker_claude_is_launched_without_a_way_to_ask(self, fake_cli: FakeCli) -> None:
        fake_cli.set_scenario({"asks": [_BASH]})

        await _turn(make_runtime(fake_cli), None)

        argv = _argv(fake_cli)
        assert "--permission-prompt-tool" not in argv
        assert "--mcp-config" not in argv
        assert not (fake_cli.bin_dir / "answers.json").exists()

    async def test_allow_all_never_asks_and_starts_no_bridge(self, fake_cli: FakeCli) -> None:
        fake_cli.set_scenario({"asks": [_BASH]})
        broker = _Broker()

        await _turn(make_runtime(fake_cli, permission_mode="allow_all"), broker)

        argv = _argv(fake_cli)
        assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
        assert "--permission-prompt-tool" not in argv
        assert broker.asked == []

    async def test_a_cli_that_cannot_ask_is_started_the_same_with_or_without_a_broker(self, fake_cli: FakeCli) -> None:
        runtime = make_runtime(fake_cli, "my-agent")

        await _turn(runtime, _Broker())
        await _turn(make_runtime(fake_cli, "my-agent"), None)

        first, second = (_argv_of(call) for call in fake_cli.calls())
        assert first == second
        assert "--mcp-config" not in first

    async def test_a_stored_prompt_tool_in_the_args_cannot_replace_the_hosts(self, fake_cli: FakeCli) -> None:
        fake_cli.set_scenario({"asks": [_BASH]})
        args = ["-p", "--output-format", "stream-json", "--permission-prompt-tool", "mcp__other__approve"]

        await _turn(make_runtime(fake_cli, args=args), _Broker())

        argv = _argv(fake_cli)
        assert argv.count("--permission-prompt-tool") == 1
        assert "mcp__other__approve" not in argv

    @pytest.mark.parametrize(
        ("command", "asks"),
        [("claude", True), ("claude-code", True), ("gemini", False), ("my-agent", False)],
    )
    def test_only_claude_declares_that_it_asks_the_host(self, command: str, asks: bool) -> None:
        runtime = CliRuntime("t", RuntimeConfig(backend_type="cli", command=command), workspace="/tmp/ws")

        assert runtime.capabilities.supports_permission_requests is asks


class TestTimeAndEndings:
    async def test_the_time_a_person_takes_is_not_the_time_claude_has(self, fake_cli: FakeCli) -> None:
        fake_cli.set_scenario({"asks": [_BASH]})
        runtime = make_runtime(fake_cli, timeout_seconds=2)

        events = await _turn(runtime, _Broker(delay=3.0))

        assert errors_of(events) == []
        assert text_of(events) == "allow"

    async def test_a_stopped_turn_withdraws_the_request_and_removes_the_bridge(self, fake_cli: FakeCli) -> None:
        fake_cli.set_scenario({"asks": [_BASH]})
        runtime = make_runtime(fake_cli)
        broker = _Broker(delay=60.0)

        async def consume() -> list[RuntimeEvent]:
            return [event async for event in runtime.run_turn("hello", "s1", permissions=broker)]

        turn = asyncio.create_task(consume())
        await asyncio.wait_for(broker.asked_event.wait(), 20)
        await runtime.cancel("s1")
        await asyncio.wait_for(turn, 20)

        assert broker.cancelled is True
        argv = _argv(fake_cli)
        assert not Path(argv[argv.index("--mcp-config") + 1]).exists()

    async def test_a_bridge_that_cannot_start_ends_the_turn_with_an_error_instead_of_running_unguarded(
        self, fake_cli: FakeCli, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def refuse(self: permission_bridge.PermissionBridge) -> permission_bridge.PermissionBridge:
            raise OSError("no loopback")

        monkeypatch.setattr(permission_bridge.PermissionBridge, "__aenter__", refuse)

        events = await _turn(make_runtime(fake_cli), _Broker())

        assert errors_of(events)
        assert fake_cli.calls() == []
