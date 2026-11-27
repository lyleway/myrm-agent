"""Real-model check that a Stop pressed while a shell step runs lets none of the file writes that were waiting behind it change a file.

[INPUT]
- POST /api/v1/agents/agent-stream with the LITE model and an agent that makes one call per listed item, all in one response: a shell step
  and two file writes (POS: the whole server path, a real provider call, the real tool gate and tools, SSE back)
- POST /api/v1/agents/chats/{chat_id}/cancel while the shell step runs (POS: what the Stop button sends)
- A step program that holds its first step for two minutes and logs every start (POS: ``tests/support/tool_order_probe.py``)
- ``StreamCompactor.put`` held for a few seconds after the Stop (POS: a run whose event path is slow when the Stop arrives)

[OUTPUT]
- Fails when a file write that was waiting wrote its file after the Stop (during the hold or after it), when the turn is still streaming after
  the hold, when a process of the turn is alive once it has ended, or when the chat does not store the turn as stopped by the user. Fails,
  rather than passes, when no attempt got the shell step running with a write waiting behind it: nothing was checked then. The control turn
  (nobody presses Stop) shows both files are written when nothing stops the batch.

[POS]
The shell step and the file writes are all exclusive calls, so they start one at a time in the order the model lists them and the writes wait
while the shell step runs. The harness refuses a waiting call once the request is stopped (``agent/middlewares/tooling/_request_stop.py``);
the file tool never looks at the request, so what keeps a stopped turn from changing a file is that refusal. A run whose event path is idle
when the Stop arrives is torn down before the next call is let through, so the held case keeps that window open. The shell step holds for two
minutes, so a turn cannot end without the Stop, and a process the turn leaves behind is killed by its marker at the end.
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from myrm_agent_harness.agent.streaming.stream_compactor import StreamCompactor

from tests.api.agent.utils import check_e2e_errors, get_lite_model_selection
from tests.support.marked_command_lines import processes_with_marker
from tests.support.real_model_workspace_turns import harness_workspace, new_chat, stream_turn, turn_request
from tests.support.tool_order_probe import BASH_STEP_KEY, STEP_LOG_NAME, TAGS, started_tags, step_script

_SHELL_TAG = TAGS[0]
_WRITE_TOOL = "file_write_tool"
_WAITING_FILES = ("waiting_one.txt", "waiting_two.txt")
_SHELL_STEP_HOLD_SECONDS = 120
_SHELL_STEP_WAIT_SECONDS = 60.0
_STOP_DELAY_SECONDS = 1.0
_HOLD_SECONDS = 3.0
_STOP_DEADLINE_SECONDS = 20.0
_SETTLE_SECONDS = 6.0
_CONTROL_TURN_SECONDS = 150.0
_MAX_ATTEMPTS = 3
# The longest an attempt can take: the shell step never starts, then the Stop, the hold, the wait for the stream to end, the settle and the read-back.
_ATTEMPT_SECONDS = _SHELL_STEP_WAIT_SECONDS + _HOLD_SECONDS + _STOP_DEADLINE_SECONDS + _SETTLE_SECONDS + 15

pytestmark = [
    pytest.mark.usefixtures("stuck_turn_report"),
    pytest.mark.e2e,
    pytest.mark.skipif(not os.environ.get("LITE_API_KEY"), reason="E2E test requires LITE_API_KEY"),
    pytest.mark.timeout(int(_MAX_ATTEMPTS * _ATTEMPT_SECONDS), method="thread"),
]


@dataclass(frozen=True)
class Batch:
    """A turn that has been sent: a shell step followed by two file writes, asked for in one response."""

    chat_id: str
    workspace: Path
    script: Path
    events: list[dict[str, object]]
    failures: list[Exception]
    worker: threading.Thread

    def shell_started(self) -> bool:
        return _SHELL_TAG in started_tags(self.workspace / STEP_LOG_NAME)

    def written(self) -> list[str]:
        return [name for name in _WAITING_FILES if (self.workspace / name).exists()]

    def announced_writes(self) -> int:
        return sum(1 for event in self.events if event.get("type") == "tasks_steps" and event.get("tool_name") == _WRITE_TOOL)

    def kill_leftovers(self) -> None:
        for pid in processes_with_marker(str(self.script)):
            os.kill(pid, signal.SIGKILL)


@dataclass(frozen=True)
class StoppedTurn:
    """What a batch showed once the Stop had been pressed on it."""

    announced_writes: int
    shell_started: bool
    written_at_stop: list[str]
    written_during_hold: list[str]
    written_after: list[str]
    ended_after_seconds: float | None
    alive: list[int]
    stop_reason: str | None
    events: list[dict[str, object]]

    @property
    def conclusive(self) -> bool:
        """Whether the shell step was running with a write still waiting behind it when the Stop was pressed."""
        return self.shell_started and self.announced_writes > 0 and not self.written_at_stop


@pytest.fixture
def event_path_hold(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """A switch that holds the run's event path: while it is set, ``StreamCompactor.put`` takes no further event."""
    held = threading.Event()
    original_put = StreamCompactor.put

    async def put(self: StreamCompactor, event: object) -> None:
        while held.is_set():
            await asyncio.sleep(0.05)
        await original_put(self, event)

    monkeypatch.setattr(StreamCompactor, "put", put)
    yield held
    held.clear()


def _create_agent(client: TestClient) -> str:
    created = client.post(
        "/api/agents",
        json={
            "name": f"Stop batch LIVE {uuid.uuid4().hex[:8]}",
            "description": "Live check of a Stop pressed during a batch of exclusive calls",
            "is_built_in": False,
            "system_prompt": (
                f"You work with {BASH_STEP_KEY} and {_WRITE_TOOL}. The tools run the calls of one response one after another, in the "
                "order you list them. When the user lists several calls, make one call per item, all in the same response and in "
                "the order listed; never wait for one result before the next call and never combine items into one call."
            ),
            "skill_ids": [],
            "mcp_ids": [],
            "enabled_builtin_tools": ["code_execute", "file_ops"],
            "security_overrides": {"yoloModeEnabled": True, "yolo_mode_enabled_at": time.time()},
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    data = body.get("data")
    agent_id = data.get("id") if isinstance(data, dict) else body.get("id")
    assert isinstance(agent_id, str) and agent_id, body
    return agent_id


def _prompt(script: Path, workspace: Path) -> str:
    writes = "\n".join(
        f"{number}. {_WRITE_TOOL}: path {workspace / name}, content {name}" for number, name in enumerate(_WAITING_FILES, start=2)
    )
    return (
        "Make these calls in your next response, in this order (the tools run them one after another in the order you list them):\n"
        f"1. {BASH_STEP_KEY}: python3 {script} {_SHELL_TAG}\n{writes}"
    )


def _send_batch(client: TestClient, agent_id: str, shell_step_seconds: float) -> Batch:
    """Send the batch in a new chat; the shell step holds for ``shell_step_seconds``."""
    chat_id = new_chat(client)
    workspace = asyncio.run(harness_workspace(chat_id))
    script = workspace / "step.py"
    script.write_text(step_script({_SHELL_TAG: shell_step_seconds}), encoding="utf-8")
    request = turn_request(_prompt(script, workspace), chat_id, get_lite_model_selection(), agent_id)
    events: list[dict[str, object]] = []
    failures: list[Exception] = []

    def stream() -> None:
        try:
            events.extend(stream_turn(client, request))
        except Exception as error:  # re-raised on the test thread
            failures.append(error)

    worker = threading.Thread(target=stream, daemon=True)
    worker.start()
    return Batch(chat_id, workspace, script, events, failures, worker)


def _wait_until(condition: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return condition()


def _stored_stop_reason(messages: list[object]) -> str | None:
    """The reason the chat stored on its latest assistant message as the end of the turn, if it stored one."""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        metadata = message.get("metadata")
        stop = metadata.get("stopReason") if isinstance(metadata, dict) else None
        detail = stop.get("detail") if isinstance(stop, dict) else None
        reason = detail.get("reason") if isinstance(detail, dict) else None
        return reason if isinstance(reason, str) else None
    return None


def _stop_one_turn(client: TestClient, agent_id: str, event_path_hold: threading.Event, hold_seconds: float) -> StoppedTurn:
    """Send the batch, press Stop once the shell step is running, and read what the turn left behind.

    The event path is held for ``hold_seconds`` from the Stop on; the turn can only end after it is let go.
    """
    batch = _send_batch(client, agent_id, _SHELL_STEP_HOLD_SECONDS)
    try:
        if _wait_until(batch.shell_started, _SHELL_STEP_WAIT_SECONDS):
            time.sleep(_STOP_DELAY_SECONDS)
        shell_started = batch.shell_started()
        written_at_stop = batch.written()
        if hold_seconds:
            event_path_hold.set()
        cancelled = client.post(f"/api/v1/agents/chats/{batch.chat_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        time.sleep(hold_seconds)
        written_during_hold = batch.written()
        event_path_hold.clear()
        released_at = time.monotonic()
        batch.worker.join(_STOP_DEADLINE_SECONDS)
        ended_after_seconds = None if batch.worker.is_alive() else time.monotonic() - released_at
        time.sleep(_SETTLE_SECONDS)
        if batch.failures:
            raise batch.failures[0]
        messages = client.get(f"/api/v1/chats/{batch.chat_id}/messages").json()["data"]["messages"]
        return StoppedTurn(
            announced_writes=batch.announced_writes(),
            shell_started=shell_started,
            written_at_stop=written_at_stop,
            written_during_hold=written_during_hold,
            written_after=batch.written(),
            ended_after_seconds=ended_after_seconds,
            alive=processes_with_marker(str(batch.script)),
            stop_reason=_stored_stop_reason(messages),
            events=batch.events,
        )
    finally:
        event_path_hold.clear()
        batch.kill_leftovers()


@pytest.mark.parametrize("hold_seconds", [0.0, _HOLD_SECONDS], ids=["event_path_free", "event_path_held"])
def test_a_stop_during_a_batch_lets_none_of_the_waiting_file_writes_change_a_file(
    client: TestClient, event_path_hold: threading.Event, hold_seconds: float
) -> None:
    agent_id = _create_agent(client)
    inconclusive: list[StoppedTurn] = []
    for _ in range(_MAX_ATTEMPTS):
        turn = _stop_one_turn(client, agent_id, event_path_hold, hold_seconds)
        check_e2e_errors(turn.events)
        if turn.conclusive:
            break
        inconclusive.append(turn)
    else:
        pytest.fail(
            "no attempt had the shell step running with a file write waiting behind it when Stop was pressed: "
            f"{[(t.shell_started, t.announced_writes, t.written_at_stop) for t in inconclusive]}"
        )

    assert turn.written_during_hold == [], f"files written while the event path was held: {turn.written_during_hold}"
    assert turn.ended_after_seconds is not None, (
        f"the turn was still streaming {_STOP_DEADLINE_SECONDS}s after the event path was let go"
    )
    assert turn.written_after == [], f"files written after Stop: {turn.written_after}"
    assert turn.alive == [], f"processes of the turn still alive after it ended: {turn.alive}"
    assert turn.stop_reason == "user_cancelled", f"stored stop reason: {turn.stop_reason}"


def test_control_a_batch_nobody_stops_writes_every_waiting_file(client: TestClient) -> None:
    batch = _send_batch(client, _create_agent(client), shell_step_seconds=0.5)
    try:
        batch.worker.join(_CONTROL_TURN_SECONDS)
        assert not batch.worker.is_alive(), f"the control turn did not end within {_CONTROL_TURN_SECONDS}s"
        if batch.failures:
            raise batch.failures[0]
        check_e2e_errors(batch.events)
        assert batch.written() == list(_WAITING_FILES), f"files written by a batch nobody stopped: {batch.written()}"
    finally:
        batch.kill_leftovers()
