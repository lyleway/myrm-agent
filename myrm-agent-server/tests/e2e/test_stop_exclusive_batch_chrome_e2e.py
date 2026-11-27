"""Chrome LIVE_AGENT E2E: the user presses the page's Stop while a shell step runs, and the file writes that waited behind it change no file.

The user types one message into the composer and presses send. It asks, in one response, for a shell step and two file writes. All three
are exclusive calls: they start one at a time in the order listed, so both writes wait while the shell step runs, and the step holds for
two minutes. The user presses Stop once the step runs. Afterwards neither file exists, no process of the turn is left, the page offers no
Stop, and the chat stored the turn as stopped by the user.

The browser leaves the server's event path idle, so the run is torn down before a waiting call is let through; the window in which such a call
could still start is held open by tests/api/agent/test_stop_exclusive_batch_live_e2e.py. This node is the user-level check that a Stop
pressed in the browser ends such a batch.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_stop_exclusive_batch_chrome_e2e.py
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cdp_chat.support import fetch_chat_messages  # noqa: E402

from tests.support.chrome_chat_page_observe import wait_until_not_streaming  # noqa: E402
from tests.support.chrome_chat_page_stop import press_stop  # noqa: E402
from tests.support.chrome_mcp_e2e import (  # noqa: E402
    ChromeMcpClient,
    McpPage,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    wait_for_state,
)
from tests.support.chrome_skill_hooks_composer import describe_page, drive_chat_turn  # noqa: E402
from tests.support.chrome_skill_hooks_live_e2e import new_agent_chat, setup_auto_approving_agent  # noqa: E402
from tests.support.e2e_runtime_guard import E2EResourceLedger, heartbeat_once  # noqa: E402
from tests.support.marked_command_lines import processes_with_marker  # noqa: E402
from tests.support.tool_order_probe import (  # noqa: E402
    BATCH_SHELL_TAG,
    BATCH_SYSTEM_PROMPT,
    STEP_LOG_NAME,
    WAITING_FILES,
    WRITE_STEP_KEY,
    batch_prompt,
    started_tags,
    step_keys_in,
    step_script,
    stored_stop_reason,
)

_AGENT_TOOLS = ("code_execute", "file_ops")
_SHELL_STEP_HOLD_SECONDS = 120
_SHELL_STEP_WAIT_SECONDS = 90.0
_STOP_DELAY_SECONDS = 1.0
_STORED_TURN_TIMEOUT_SECONDS = 60.0
_SETTLE_SECONDS = 6.0
_MAX_ATTEMPTS = 3
# The longest an attempt can take: the shell step never starts, Stop is offered late, the page and the stored turn are slow to settle.
_ATTEMPT_SECONDS = _SHELL_STEP_WAIT_SECONDS + 70.0 + 60.0 + _STORED_TURN_TIMEOUT_SECONDS + _SETTLE_SECONDS
# Another attempt only starts when a whole attempt still fits in the test's own timeout.
_TEST_BUDGET_SECONDS = 540.0

# The agent reaches the page after it opens the chat: a message sent earlier runs on the defaults.
_AGENT_BOUND_JS = """((agentId) => {
  const bound = window.__myrmChatStore?.getState?.()?.agentConfig?.agentId ?? null;
  return { ready: bound === agentId, bound };
})"""


def _trace(stage: str, detail: str) -> None:
    print(f"E2E_STOP_BATCH_{stage}: {detail}", flush=True)


@dataclass(frozen=True)
class StoppedBatch:
    """What a batch showed once the user had pressed Stop on it."""

    chat_id: str
    shell_started: bool
    announced_writes: int
    written_at_stop: list[str]
    written_after: list[str]
    alive: list[int]
    stop_reason: str | None
    page_reaction: dict[str, object]

    @property
    def conclusive(self) -> bool:
        """Whether the shell step was running with a write the model had announced still waiting behind it when Stop was pressed."""
        return self.shell_started and self.announced_writes > 0 and not self.written_at_stop


def _written(workspace: Path) -> list[str]:
    return [name for name in WAITING_FILES if (workspace / name).exists()]


def _wait_until(condition: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        heartbeat_once()
        if condition():
            return True
        time.sleep(0.5)
    return condition()


def _stored_stop_reason_of(chat_id: str, *, api_base: str) -> str | None:
    """The chat stores the stopped turn once the server's run has ended."""
    deadline = time.monotonic() + _STORED_TURN_TIMEOUT_SECONDS
    reason = stored_stop_reason(fetch_chat_messages(chat_id, api_url=api_base))
    while reason is None and time.monotonic() < deadline:
        heartbeat_once()
        time.sleep(2.0)
        reason = stored_stop_reason(fetch_chat_messages(chat_id, api_url=api_base))
    return reason


def _stop_one_batch(tmp_path: Path, ledger: E2EResourceLedger, agent_id: str, attempt: int) -> StoppedBatch:
    """Send the batch from the composer of a new chat, press Stop once the shell step runs, and read what the turn left behind."""
    api_base, ui_base = get_e2e_api_url(), get_e2e_ui_url()
    probe = new_agent_chat(tmp_path, ledger, f"stopbatch{attempt}", agent_id)
    seeded = http_json("POST", f"{api_base}/api/v1/chats/test/seed-file-edit-batch-workspace?chat_id={probe.chat_id}")
    assert isinstance(seeded, dict) and str(seeded.get("chat_id")) == probe.chat_id, seeded
    workspace = Path(str(seeded["file_path"])).parent
    script = workspace / "step.py"
    script.write_text(step_script({BATCH_SHELL_TAG: _SHELL_STEP_HOLD_SECONDS}), encoding="utf-8")
    log = workspace / STEP_LOG_NAME
    assert not log.exists(), f"the log must not exist before the turn: {log}"
    chat_url = f"{ui_base.rstrip('/')}{probe.ui_path}"
    stopped: list[StoppedBatch] = []

    def agent_bound(client: ChromeMcpClient, page: McpPage) -> None:
        bound = wait_for_state(client, page, f"({_AGENT_BOUND_JS})({json.dumps(agent_id)})", timeout_sec=90.0, page_url=chat_url)
        assert bound.get("ready") is True, bound

    def stop_the_batch(client: ChromeMcpClient, page: McpPage) -> None:
        shell_started = _wait_until(lambda: BATCH_SHELL_TAG in started_tags(log), _SHELL_STEP_WAIT_SECONDS)
        if shell_started:
            time.sleep(_STOP_DELAY_SECONDS)
        written_at_stop = _written(workspace)
        _trace("stop", f"chat={probe.chat_id} shell_started={shell_started} written_at_stop={written_at_stop}")
        reaction: dict[str, object] = {}
        stop_pressed = False
        try:
            # The page sent this turn itself, so its Stop may depend on the server's status alone (as in the Stop-button node).
            reaction = press_stop(client, page, require_controller=False)
            stop_pressed = True
        except AssertionError as error:
            # Without a shell step the model may simply have ended its turn: only a Stop that a running step needed is a finding.
            if shell_started:
                raise AssertionError(f"{error}; {describe_page(client, page)}") from error
        wait_until_not_streaming(client, page)
        stop_reason = _stored_stop_reason_of(probe.chat_id, api_base=api_base) if stop_pressed else None
        time.sleep(_SETTLE_SECONDS)
        stored_keys = step_keys_in(fetch_chat_messages(probe.chat_id, api_url=api_base))
        stopped.append(
            StoppedBatch(
                chat_id=probe.chat_id,
                shell_started=shell_started,
                announced_writes=sum(1 for key in stored_keys if key.startswith(WRITE_STEP_KEY)),
                written_at_stop=written_at_stop,
                written_after=_written(workspace),
                alive=processes_with_marker(str(script)),
                stop_reason=stop_reason,
                page_reaction=reaction,
            )
        )

    try:
        drive_chat_turn(
            ui_base,
            probe,
            batch_prompt(script, workspace),
            invoke_skill=False,
            before_send=agent_bound,
            while_open=stop_the_batch,
        )
    finally:
        for pid in processes_with_marker(str(script)):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
    assert stopped, f"the page was left before Stop was handled; chat={probe.chat_id}"
    return stopped[0]


@pytest.mark.chrome_e2e(execution_mode="PRIVATE", access_scope="NAMESPACE_WRITE", workload="LIVE", private_reason="live_shpoib")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_stop_pressed_while_a_shell_step_runs_changes_no_file_that_waited_behind_it(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    test_started = time.monotonic()
    agent_id = setup_auto_approving_agent(e2e_resource_ledger, BATCH_SYSTEM_PROMPT, _AGENT_TOOLS)
    _trace("ready", f"agent={agent_id}")

    inconclusive: list[StoppedBatch] = []
    conclusive: StoppedBatch | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        batch = _stop_one_batch(tmp_path, e2e_resource_ledger, agent_id, attempt)
        _trace("stopped", f"attempt={attempt} {batch}")
        if batch.conclusive:
            conclusive = batch
            break
        inconclusive.append(batch)
        if time.monotonic() - test_started + _ATTEMPT_SECONDS > _TEST_BUDGET_SECONDS:
            break

    assert conclusive is not None, (
        "no attempt had the shell step running with a file write waiting behind it when Stop was pressed: "
        f"{[(b.shell_started, b.announced_writes, b.written_at_stop) for b in inconclusive]}"
    )
    assert conclusive.written_after == [], f"files written after Stop: {conclusive.written_after}; {conclusive}"
    assert conclusive.alive == [], f"processes of the turn still alive after it ended: {conclusive.alive}; {conclusive}"
    assert conclusive.stop_reason == "user_cancelled", f"stored stop reason: {conclusive.stop_reason}; {conclusive}"
