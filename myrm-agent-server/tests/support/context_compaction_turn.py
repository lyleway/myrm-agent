"""Driving one real turn in a compaction Chrome E2E and reading what the page and the server made of it.

[INPUT]
- cdp_chat.support (POS: the messages the runtime under test stored for a chat)
- tests.support.chrome_mcp_e2e (POS: CDP page helpers, http_json, wait_for_state)
- tests.support.context_compaction_e2e (POS: the idle-gate setting a test agent carries)

[OUTPUT]
- idle_compacting_agent(): a user agent that compacts a chat quiet for half an hour before its next reply, removed on exit
- record_compaction_phases(): taps the stream recorder, so the compaction's phases are kept at the moment they arrive
- ask_in_chat(): sends a prompt and waits until the reply is on screen and stored; returns what the page saw of the turn
- completed_replies() / wait_new_reply_stored(): how many replies the server holds as complete, and the wait for one more (a turn the page started some other way, such as a regenerate or an edit)
- TurnEvidence: the compaction phases on the stream, the steps and text of the reply
- steps_panel_js(): expands the reply's progress steps until they name a title in this language

[POS]
A turn here is the real thing: the page sends the prompt, the agent's model answers. The page's own store is what shows
whether the reply carries a compaction step, and the server's stored reply is what says the turn has really ended.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from cdp_chat.support import fetch_chat_messages

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, http_json, wait_for_state
from tests.support.context_compaction_e2e import IDLE_COMPACT_AFTER_SECONDS

# Compaction by the real model, then the reply: both must fit.
TURN_TIMEOUT_SEC = 420.0

# The recorder of the E2E bridge keeps only the last 64 stream events and the token deltas flood it, so the phases of
# the compaction (sent before the first token) are taken at the moment they arrive. A property keeps the tap in place
# if the bridge installs its recorder again.
_RECORD_COMPACTION_PHASES_JS = """(() => {
  const name = '__MYRM_E2E_RECORD_SSE__';
  if (window.__e2eCompactionPhases) return { ok: true, installed: false };
  const phases = [];
  window.__e2eCompactionPhases = phases;
  const tap = (record) => (type, messageId, data) => {
    const phase = data && typeof data === 'object' ? data.phase : undefined;
    if (type === 'status' && typeof phase === 'string') phases.push(phase);
    return typeof record === 'function' ? record(type, messageId, data) : undefined;
  };
  let tapped = tap(window[name]);
  Object.defineProperty(window, name, { configurable: true, get: () => tapped, set: (record) => { tapped = tap(record); } });
  return { ok: true, installed: true };
})()"""

_STORE_EVIDENCE_JS = """(() => {
  const messages = window.__myrmChatStore?.getState?.()?.messages ?? [];
  const reply = [...messages].reverse().find((m) => m.role === 'assistant');
  const steps = reply?.progressSteps ?? reply?.metadata?.progressSteps ?? [];
  return {
    phases: window.__e2eCompactionPhases ?? null,
    steps: steps.map((step) => ({ key: step.step_key, status: step.status })),
    reply: String(reply?.content ?? ''),
  };
})()"""


def _send_turn_js(prompt: str) -> str:
    return f"""(async () => {{
  const bridge = window.__MYRM_E2E_CHAT__;
  if (!bridge?.sendChatMessage) return {{ ok: false, err: 'no-sendChatMessage' }};
  bridge.setActionMode?.('agent');
  const before = bridge.turnSnapshot?.() ?? {{}};
  const usersBefore = before.userCount ?? 0;
  const result = await bridge.sendChatMessage({json.dumps(prompt)}, {{
    baselineUserCount: usersBefore,
    preserveActionMode: true,
  }});
  return {{ ...result, assistantsBefore: before.assistantCount ?? 0 }};
}})()"""


def _turn_finished_js(assistants_before: int) -> str:
    """Ready once the stream has ended and the turn added an assistant message with words in it.

    The page loads only the newest part of a long chat, so the count to beat is the one it showed before the send.
    """
    return f"""(() => {{
  const snap = window.__MYRM_E2E_CHAT__?.turnSnapshot?.() ?? {{}};
  return {{
    ready: snap.isStreaming === false && (snap.assistantCount ?? 0) > {assistants_before} && !!(snap.lastAssistantSample || '').trim(),
    isStreaming: snap.isStreaming,
    assistantCount: snap.assistantCount,
    sample: (snap.lastAssistantSample || '').slice(0, 200),
  }};
}})()"""


def steps_panel_js(locale: str, title: str) -> str:
    """Expand the reply's progress steps; ready once the panel is in this language and names ``title``.

    Folded, the panel shows only the latest step; the toggle's own state says whether it is open, so a re-poll never
    folds it again.
    """
    return f"""(() => {{
  const toggle = document.querySelector('[data-testid="progress-steps-toggle"]');
  if (toggle && toggle.getAttribute('data-expanded') !== 'true') toggle.click();
  const panel = document.querySelector('[data-testid="progress-steps-panel"]');
  const text = panel ? panel.innerText : '';
  return {{
    ready: text.includes({json.dumps(title)}) && document.documentElement.lang.startsWith({json.dumps(locale)}),
    lang: document.documentElement.lang,
    hasToggle: !!toggle,
    text: text.slice(0, 600),
  }};
}})()"""


def completed_replies(chat_id: str, api_url: str) -> int:
    """How many assistant replies the server has stored and marked complete (0 while the chat cannot be read)."""
    try:
        stored = [m for m in fetch_chat_messages(chat_id, api_url=api_url) if isinstance(m, dict)]
    except OSError:
        return 0
    return sum(
        1
        for message in stored
        if message.get("role") == "assistant"
        and isinstance(message.get("metadata"), dict)
        and message["metadata"].get("completionStatus") == "complete"
    )


def wait_new_reply_stored(chat_id: str, api_url: str, *, completed_before: int) -> None:
    """Wait until the server has stored one more reply it marked complete, so the summary it wrote can be read."""
    deadline = time.monotonic() + TURN_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if completed_replies(chat_id, api_url) > completed_before:
            return
        time.sleep(2.0)
    pytest.fail(f"no new completed reply for chat {chat_id} within {TURN_TIMEOUT_SEC}s (had {completed_before})")


@dataclass(frozen=True)
class TurnEvidence:
    """What the page itself saw of the turn: the compaction phases on the stream and the reply with its steps."""

    phases: list[object]
    steps: list[dict[str, object]]
    reply: str

    @property
    def step_keys(self) -> list[object]:
        return [step.get("key") for step in self.steps]

    @property
    def compaction_steps(self) -> list[tuple[object, object]]:
        """The reply's compaction steps as ``(key, status)``; a reply the gate left alone has none."""
        return [
            (step["key"], step.get("status")) for step in self.steps if str(step.get("key", "")).startswith("context_compaction")
        ]


def record_compaction_phases(client: ChromeMcpClient, page: McpPage) -> None:
    tap = client.evaluate(page, _RECORD_COMPACTION_PHASES_JS, timeout_sec=15.0)
    assert isinstance(tap, dict) and tap.get("ok") is True, f"stream recorder not found: {tap!r}"


def _read_turn_evidence(client: ChromeMcpClient, page: McpPage) -> TurnEvidence:
    seen = client.evaluate(page, _STORE_EVIDENCE_JS, timeout_sec=15.0)
    assert isinstance(seen, dict), seen
    phases, steps, reply = seen.get("phases"), seen.get("steps"), seen.get("reply")
    assert isinstance(phases, list), f"the stream recorder was lost with the page: {seen!r}"
    assert isinstance(steps, list) and isinstance(reply, str), seen
    return TurnEvidence(phases=phases, steps=[step for step in steps if isinstance(step, dict)], reply=reply)


def ask_in_chat(
    client: ChromeMcpClient, page: McpPage, page_url: str, *, chat_id: str, api_url: str, prompt: str
) -> TurnEvidence:
    """Send ``prompt`` as the user would and wait until its reply is on screen and stored; returns what the page saw."""
    completed_before = completed_replies(chat_id, api_url)
    sent = client.evaluate(page, _send_turn_js(prompt), timeout_sec=60.0)
    assert isinstance(sent, dict) and sent.get("ok") is True, f"send failed: {sent!r}"
    assistants_before = sent.get("assistantsBefore")
    assert isinstance(assistants_before, int), f"send reported no assistant count: {sent!r}"
    wait_for_state(client, page, _turn_finished_js(assistants_before), timeout_sec=TURN_TIMEOUT_SEC, page_url=page_url)
    wait_new_reply_stored(chat_id, api_url, completed_before=completed_before)
    return _read_turn_evidence(client, page)


@contextmanager
def idle_compacting_agent(api_url: str) -> Iterator[str]:
    """A user agent that compacts a chat quiet for ``IDLE_COMPACT_AFTER_SECONDS`` before its next reply; removed on exit."""
    created = http_json(
        "POST",
        f"{api_url}/api/v1/user-agents",
        body={
            "name": f"idle-compact-e2e-{uuid.uuid4().hex[:8]}",
            "system_prompt": "You are a concise project assistant. Answer from the conversation so far.",
            "engine_params": {"idle_compact_after_seconds": IDLE_COMPACT_AFTER_SECONDS},
        },
    )
    agent_id = (created.get("data") or {}).get("id") if isinstance(created, dict) else None
    assert isinstance(agent_id, str) and agent_id, f"failed to create the agent: {created}"
    try:
        yield agent_id
    finally:
        http_json("DELETE", f"{api_url}/api/v1/user-agents/{agent_id}", expected_statuses=frozenset({200, 204, 404}))
