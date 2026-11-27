"""Chrome LIVE E2E: a todo_write plan reaches the open chat's plan panel while the turn is still running.

A real model writes a three-item plan with ``todo_write`` and then updates it. A page-side recorder pairs every
``plan_update`` stream event with what the plan panel shows the user (``tests/support/plan_panel_probe.py``), so one
real turn covers the whole chain: the tool's write, one event per write, the panel's re-read of the plan endpoint and
the rendered list, all before the turn's terminal event. Nothing upstream of the page is stubbed.

Contract asserted:
* the stream carries exactly one ``plan_update`` per write: its revisions run 1..N and N is the revision the plan
  endpoint serves;
* the panel shows the last event's plan to the user before the turn ends, and an earlier event's plan too when the
  model wrote more than once;
* the plan endpoint serves the last event's plan, and a reloaded chat shows it again.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_plan_panel_live_chrome_e2e.py
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cdp_chat.mcp_ui import McpChatSession  # noqa: E402
from cdp_chat.support import e2e_runtime_bootstrap_apply_js, get_e2e_api_url, wait_e2e_provider_ready  # noqa: E402
from dev_gate.contract import EvaluateIntent  # noqa: E402
from e2e_core.orchestrator import remaining_wall_sec, touch_wall_progress  # noqa: E402

from tests.support.chrome_mcp_e2e import get_e2e_ui_url, http_json, open_mcp_page, prepare_e2e_ui_session  # noqa: E402
from tests.support.e2e_lite_model_pin import pin_lite_model_for_e2e  # noqa: E402
from tests.support.e2e_provider_seed import seed_live_e2e_providers  # noqa: E402
from tests.support.e2e_runtime_guard import E2EResourceLedger, heartbeat_once  # noqa: E402
from tests.support.plan_panel_probe import (  # noqa: E402
    DUMP_JS,
    PANEL_JS,
    RECORDER_JS,
    TURN_PROBE_JS,
    Step,
    Turn,
    as_dict,
    check_turn,
    panel_steps,
    parse_turn,
    prompt,
    timeline,
)

_MAX_TURN_ATTEMPTS = 3
_TURN_TIMEOUT_SEC = 300.0
# BODY wall budget kept back for the reload check that ends the test: navigate, panel re-read, slack.
_RELOAD_RESERVE_SEC = 150.0
_RELOAD_WAIT_SEC = 90.0

_AGENT_READY_JS = """(() => {
  const bridge = window.__MYRM_E2E_CHAT__;
  const debug = bridge?.debugProviderState?.() ?? {};
  return { ready: !!bridge?.handleSubmit && !!debug.selection, selection: debug.selection ?? null };
})()"""

_ENSURE_CHAT_SESSION_JS = """(() => {
  const bridge = window.__MYRM_E2E_CHAT__;
  if (!bridge?.ensureChatSession) return { ok: false, err: 'no ensureChatSession' };
  return bridge.ensureChatSession().then(() => ({ ok: true }));
})()"""


def _trace(stage: str, detail: str) -> None:
    print(f"E2E_PLAN_PANEL_{stage}: {detail}", flush=True)


def _beat() -> None:
    """Report liveness to the coordinator and the BODY stall guard: the waits below poll for minutes."""
    heartbeat_once()
    touch_wall_progress()


def _create_agent(api_url: str) -> str:
    payload: dict[str, object] = {
        "name": f"Plan panel LIVE {uuid.uuid4().hex[:8]}",
        "description": "Chrome LIVE E2E for the plan panel",
        "system_prompt": "You are a test agent. Follow the user's tool instructions literally.",
        "skill_ids": [],
        "mcp_ids": [],
        "enabled_builtin_tools": ["planning"],
    }
    created = as_dict(http_json("POST", f"{api_url}/api/v1/user-agents", payload))
    data = created.get("data")
    agent_id = data.get("id") if isinstance(data, dict) else created.get("id")
    assert isinstance(agent_id, str) and agent_id, created
    return agent_id


def _read_plan(api_url: str, chat_id: str) -> dict[str, object] | None:
    """The plan the server serves for the chat; ``None`` when the chat has none."""
    plan = as_dict(http_json("GET", f"{api_url}/api/v1/goals/{chat_id}/plan")).get("plan")
    return as_dict(plan) if plan is not None else None


async def _wait_turn_ended(chat: McpChatSession) -> None:
    """Wait for the terminal event: the newest assistant message carries its completion status."""
    budget = min(_TURN_TIMEOUT_SEC, max(30.0, remaining_wall_sec() - _RELOAD_RESERVE_SEC))
    deadline = time.monotonic() + budget
    state: object = None
    while time.monotonic() < deadline:
        _beat()
        state = await chat.evaluate(TURN_PROBE_JS, intent=EvaluateIntent.BRIDGE_POLL)
        if as_dict(state).get("ended") is True:
            return
        await asyncio.sleep(1.5)
    raise AssertionError(f"the turn did not end within {budget:.0f}s: {state}")


async def _run_turn(chat: McpChatSession, ui_base: str, ledger: E2EResourceLedger) -> Turn | None:
    """One real turn in a new chat; ``None`` when the page lost the recorder after the send."""
    await chat.click_new_chat()
    await chat.ensure_chat_surface(ui_base)
    ensured = as_dict(await chat.evaluate(_ENSURE_CHAT_SESSION_JS, intent=EvaluateIntent.ROUTE_ATTACH))
    assert ensured.get("ok") is True, ensured
    chat_id = str((await chat.bridge_chat_id()) or "").strip()
    assert chat_id, "expected a client chat id after opening a new chat"

    installed = as_dict(await chat.evaluate(RECORDER_JS, intent=EvaluateIntent.SYNC_PROBE))
    assert installed.get("hasStreamTap") and installed.get("hasStore"), installed

    text = prompt(uuid.uuid4().hex[:6])
    sent = await chat.send_message(text, text)
    sent_ids = [as_dict(sent.get(key) or {}).get("chatId") for key in ("started", "submit")]
    chat_id = next((str(sent_id).strip() for sent_id in sent_ids if sent_id), chat_id)
    _beat()
    await chat.wait_stream_started(text, timeout_sec=120.0, chat_id_hint=chat_id)
    ledger.register("chat", chat_id)
    _trace("SENT", f"chat={chat_id}")

    # The recorder lives in the page: a page the send reloaded took it along and its events cannot be recovered.
    if as_dict(await chat.evaluate(TURN_PROBE_JS, intent=EvaluateIntent.SYNC_PROBE)).get("hasRecorder") is not True:
        return None
    await _wait_turn_ended(chat)
    turn = parse_turn(chat_id, await chat.evaluate(DUMP_JS, intent=EvaluateIntent.SYNC_PROBE))
    _trace("TIMELINE", timeline(turn))
    return turn


async def _open_agent_page(chat: McpChatSession, agent_url: str) -> None:
    """Bind the page to this run's backend, wait for the chat bridge to carry the agent, and pin the lite model."""
    await chat.bootstrap(agent_url, timeout_sec=180.0)
    bootstrap_js = e2e_runtime_bootstrap_apply_js()
    if bootstrap_js:
        applied = as_dict(await chat.evaluate(bootstrap_js, intent=EvaluateIntent.AGENT_SUBMIT))
        assert applied.get("ok") is True, f"E2E runtime bootstrap failed: {applied}"
    else:
        await chat.ensure_e2e_api_base_binding()
    await chat.dismiss_modals()
    deadline = time.monotonic() + 90.0
    ready: dict[str, object] = {}
    while time.monotonic() < deadline:
        _beat()
        ready = as_dict(await chat.evaluate(_AGENT_READY_JS, intent=EvaluateIntent.BRIDGE_POLL))
        if ready.get("ready") is True:
            break
        await asyncio.sleep(1.0)
    assert ready.get("ready") is True, f"E2E chat bridge not ready after loading the agent: {ready}"
    await pin_lite_model_for_e2e(chat)


async def _wait_reloaded_panel(chat: McpChatSession, expected: list[Step]) -> tuple[list[Step], bool]:
    """The panel of a freshly loaded chat: its rows once they equal ``expected``, else the last rows seen."""
    deadline = time.monotonic() + _RELOAD_WAIT_SEC
    rows: tuple[list[Step], bool] = ([], False)
    while time.monotonic() < deadline:
        _beat()
        rows = panel_steps(as_dict(await chat.evaluate(PANEL_JS, intent=EvaluateIntent.BRIDGE_POLL))["steps"])
        if rows[0] == expected:
            break
        await asyncio.sleep(2.0)
    return rows


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.asyncio
async def test_todo_write_plan_reaches_the_plan_panel_during_the_turn_live(e2e_resource_ledger: E2EResourceLedger) -> None:
    api_base = get_e2e_api_url()
    prepare_e2e_ui_session(api_base)
    seed_live_e2e_providers(api_base)
    if not wait_e2e_provider_ready(timeout_sec=90.0):
        pytest.fail("Provider config not ready for the live plan panel E2E - run via ./myrm-dev test -m chrome_e2e")

    ui_base = get_e2e_ui_url().rstrip("/")
    agent_id = _create_agent(api_base)
    e2e_resource_ledger.register("agent", agent_id)
    agent_url = f"{ui_base}/?agentId={agent_id}"
    outcomes: list[str] = []
    exercised: Turn | None = None

    with open_mcp_page(agent_url, timeout_ms=120_000) as (client, page):
        chat = McpChatSession(client, page)
        await _open_agent_page(chat, agent_url)

        for attempt in range(1, _MAX_TURN_ATTEMPTS + 1):
            turn = await _run_turn(chat, ui_base, e2e_resource_ledger)
            if turn is None:
                outcomes.append(f"attempt {attempt}: the page lost the recorder")
            elif (plan := _read_plan(api_base, turn.chat_id)) is None:
                assert not turn.events, f"plan_update events for a chat the server serves no plan for: {turn.events}"
                outcomes.append(f"attempt {attempt}: the model wrote no plan")
            elif problems := check_turn(turn, plan):
                outcomes.append(f"attempt {attempt}: {'; '.join(problems)}")
            else:
                exercised = turn
                break
            if remaining_wall_sec() < _TURN_TIMEOUT_SEC + _RELOAD_RESERVE_SEC:
                break

        assert exercised is not None, f"no turn showed a plan written twice reaching the panel: {outcomes}"

        # A reloaded chat reads the plan from the server again: the panel lists the plan the last event carried.
        expected = exercised.events[-1].steps
        await chat.navigate_to_chat(exercised.chat_id, ui_base)
        reloaded, visible = await _wait_reloaded_panel(chat, expected)
        _trace("RELOADED", f"chat={exercised.chat_id} rows={reloaded} visible={visible}")
        assert reloaded == expected, f"a reloaded chat must list the served plan {expected}: {reloaded}"
        assert visible, f"the reloaded panel listed the plan out of the user's sight: {reloaded}"
