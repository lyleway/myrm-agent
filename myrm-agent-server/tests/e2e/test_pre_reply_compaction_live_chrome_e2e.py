"""Chrome E2E: a chat that went quiet for hours is compacted before the model answers, and the answer survives it.

A user comes back to a long chat after three hours and asks about something said at its start. The agent is set to
compact a chat that has been quiet for half an hour, so the real model writes a summary of the history first, the page
shows the compaction as a step of the new reply in the page's language (zh and en), and the reply then comes from the
summary. The turn's own message must not count as activity: it is stored a moment before the gate looks, and the chat
has to be judged by the message before it. The step arrives before the reply exists, so the page has to open the reply
for it.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

import pytest
from cdp_chat.support import fetch_chat_messages, get_e2e_api_url, get_e2e_ui_url, wait_e2e_provider_ready

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, http_json, prepare_e2e_ui_session, wait_for_state
from tests.support.context_compaction_e2e import (
    IDLE_COMPACT_AFTER_SECONDS,
    PROJECT_CODENAME,
    chat_page,
    create_dormant_chat,
    declare_context_window,
    ensure_locale,
    locale_text,
    open_chat,
    read_compaction_state,
)
from tests.support.e2e_provider_seed import seed_live_e2e_providers

_PROMPTS = {
    "zh": "我们的数据中心迁移项目，代号和迁移窗口分别是什么？请只用一句话回答。",
    "en": "What are the codename and the migration window of our data center migration project? Answer in one sentence.",
}
# Compaction by the real model, then the reply: both must fit.
_TURN_TIMEOUT_SEC = 420.0

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


def _steps_panel_js(locale: str, title: str) -> str:
    """Expand the reply's progress steps; ready once the panel is in this language and names the compaction.

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


def _wait_reply_stored(chat_id: str, api_url: str) -> None:
    """Wait until the server has stored a reply it marked complete, so the summary it wrote can be read."""
    deadline = time.monotonic() + _TURN_TIMEOUT_SEC
    stored: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        try:
            stored = [m for m in fetch_chat_messages(chat_id, api_url=api_url) if isinstance(m, dict)]
        except OSError:
            stored = []
        for message in reversed(stored):
            metadata = message.get("metadata")
            if (
                message.get("role") == "assistant"
                and isinstance(metadata, dict)
                and metadata.get("completionStatus") == "complete"
            ):
                return
        time.sleep(2.0)
    pytest.fail(f"no completed reply for chat {chat_id} within {_TURN_TIMEOUT_SEC}s; stored={json.dumps(stored)[-800:]}")


@dataclass(frozen=True)
class _TurnEvidence:
    """What the page itself saw of the turn: the compaction phases on the stream and the reply with its steps."""

    phases: list[object]
    steps: list[dict[str, object]]
    reply: str

    @property
    def step_keys(self) -> list[object]:
        return [step.get("key") for step in self.steps]


def _read_turn_evidence(client: ChromeMcpClient, page: McpPage) -> _TurnEvidence:
    seen = client.evaluate(page, _STORE_EVIDENCE_JS, timeout_sec=15.0)
    assert isinstance(seen, dict), seen
    phases, steps, reply = seen.get("phases"), seen.get("steps"), seen.get("reply")
    assert isinstance(phases, list), f"the stream recorder was lost with the page: {seen!r}"
    assert isinstance(steps, list) and isinstance(reply, str), seen
    return _TurnEvidence(phases=phases, steps=[step for step in steps if isinstance(step, dict)], reply=reply)


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.parametrize("locale", ["zh", "en"])
def test_chat_quiet_for_hours_is_compacted_before_the_reply_with_live_model(locale: str) -> None:
    """Ask about the start of a three-hour-old chat: the compaction step shows first, then an answer from the summary."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    endpoints = seed_live_e2e_providers(api_url)
    declare_context_window(api_url, endpoints)
    if not wait_e2e_provider_ready(timeout_sec=90.0):
        pytest.fail("Provider not ready; verify the .env.test LLM credentials")

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
        chat_id = f"e2eprereplycompact{uuid.uuid4().hex[:8]}"
        create_dormant_chat(api_url, chat_id, agent_id=agent_id)
        before = read_compaction_state(api_url, chat_id)
        assert before["summary_persisted"] is False and before["tokens_saved"] == 0, before

        with chat_page(ui_url, chat_id) as (client, page, page_url):
            ensure_locale(client, page, locale, page_url=page_url)
            open_chat(client, page, chat_id, page_url, agent_id=agent_id)
            tap = client.evaluate(page, _RECORD_COMPACTION_PHASES_JS, timeout_sec=15.0)
            assert isinstance(tap, dict) and tap.get("ok") is True, f"stream recorder not found: {tap!r}"

            sent = client.evaluate(page, _send_turn_js(_PROMPTS[locale]), timeout_sec=60.0)
            assert isinstance(sent, dict) and sent.get("ok") is True, f"send failed: {sent!r}"
            assistants_before = sent.get("assistantsBefore")
            assert isinstance(assistants_before, int), f"send reported no assistant count: {sent!r}"
            wait_for_state(client, page, _turn_finished_js(assistants_before), timeout_sec=_TURN_TIMEOUT_SEC, page_url=page_url)
            _wait_reply_stored(chat_id, api_url)

            seen = _read_turn_evidence(client, page)
            # The step opens when the compaction starts and closes on exactly one phase.
            assert seen.phases[:2] == ["active", "completed"], f"compaction phases on the stream: {seen.phases}"
            compaction = [step for step in seen.steps if str(step.get("key", "")).startswith("context_compaction")]
            assert [(step["key"], step.get("status")) for step in compaction] == [("context_compaction_completed", "complete")], (
                f"steps on the reply: {seen.steps}"
            )

            title = locale_text(locale, "progressSteps", "context_compaction_completed")
            wait_for_state(client, page, _steps_panel_js(locale, title), timeout_sec=60.0, page_url=page_url)

        stored = read_compaction_state(api_url, chat_id)
        assert stored["summary_persisted"] is True, f"the quiet chat was not compacted: {stored}"
        saved = stored["tokens_saved"]
        assert isinstance(saved, int) and saved > 0, stored
        assert PROJECT_CODENAME in str(stored["summary"]), f"codename lost from summary: {str(stored['summary'])[:600]}"
        assert PROJECT_CODENAME.lower() in seen.reply.lower(), f"the reply does not carry the codename: {seen.reply!r}"
        print(
            f"E2E_PRE_REPLY_COMPACTION locale={locale} chat={chat_id} agent={agent_id} phases={seen.phases} steps={seen.step_keys}"
        )
        print(f"E2E_PRE_REPLY_COMPACTION saved={saved} reply={seen.reply!r}")
    finally:
        http_json("DELETE", f"{api_url}/api/v1/user-agents/{agent_id}", expected_statuses=frozenset({200, 204, 404}))
