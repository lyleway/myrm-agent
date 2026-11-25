"""Chrome E2E: web_fetch normal-mode settings card (Lane-B) and live agent web_fetch_tool turn (Lane-C)."""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cdp_chat.support import (  # noqa: E402
    fetch_config_value,
    get_e2e_api_url,
    put_config_value,
    snapshot_backend_log_offset,
    wait_e2e_provider_ready,
)

from tests.support.chrome_mcp_e2e import (  # noqa: E402
    ChromeMcpClient,
    McpPage,
    _warm_ui_parallel_wait_sec,
    dismiss_blocking_modals,
    get_e2e_ui_url,
    http_json,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)
from tests.support.chrome_skill_hooks_composer import drive_chat_turn  # noqa: E402
from tests.support.chrome_skill_hooks_live_e2e import SkillChat  # noqa: E402
from tests.support.chrome_skill_hooks_observe import completed_assistant_replies, stored_messages  # noqa: E402
from tests.support.e2e_provider_seed import seed_live_e2e_providers  # noqa: E402
from tests.support.e2e_runtime_guard import E2EResourceLedger  # noqa: E402

_LIVE_FETCH_PROMPT = (
    "请务必调用 web_fetch_tool 读取 https://example.com/ 的页面标题与正文，"
    "并用中文一句话总结页面内容（回复中请保留原文标题 Example Domain）。"
)

_EXPAND_AND_EXERCISE_CARD_JS = """(() => {
  const sections = Array.from(document.querySelectorAll('section'));
  const card = sections.find((el) =>
    /Remote Web Fetch Fallback|网页抓取远程兜底/i.test(el.textContent || '')
  );
  if (!card) return { ready: false, reason: 'card_missing', path: location.pathname };

  const headerBtn = card.querySelector('button[aria-expanded]');
  if (!headerBtn) return { ready: false, reason: 'expand_btn_missing' };
  if (headerBtn.getAttribute('aria-expanded') !== 'true') {
    headerBtn.click();
    return { ready: false, reason: 'expanding_card' };
  }

  const cardText = card.textContent || '';
  const leakedInternalTerms = /Turn 1 tools|prompt cache|Turn 1 工具装载|Prompt 缓存/i.test(cardText);

  const jinaInput = card.querySelector('input[type="password"]');
  const baseInput = card.querySelector('input[placeholder="https://api.firecrawl.dev"]');
  if (!jinaInput || !baseInput) {
    return { ready: false, reason: 'inputs_missing', cardText: cardText.slice(0, 400) };
  }

  const setInputValue = (el, val) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(el, val);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };

  if (!window.__wfE2eStep) {
    const passwordBefore = card.querySelectorAll('input[type="password"]').length;
    setInputValue(jinaInput, 'jina-e2e-live-key');
    setInputValue(baseInput, 'https://fc.e2e.example');
    window.__wfE2eStep = { passwordBefore };
    return { ready: false, reason: 'waiting_for_custom_base_render' };
  }

  const passwordInputs = Array.from(card.querySelectorAll('input[type="password"]'));
  if (passwordInputs.length < 2) {
    return { ready: false, reason: 'dedicated_key_input_not_revealed', count: passwordInputs.length };
  }

  const fcKeyInput = passwordInputs[1];
  if (fcKeyInput.value !== 'fc-e2e-dedicated-key') {
    setInputValue(fcKeyInput, 'fc-e2e-dedicated-key');
    return { ready: false, reason: 'setting_dedicated_fc_key' };
  }

  const switches = Array.from(card.querySelectorAll('button[role="switch"]'));
  const inheritSwitch = switches[1];
  const isolationCheckbox = card.querySelector('input[type="checkbox"]');
  return {
    ready: true,
    leakedInternalTerms,
    passwordBefore: window.__wfE2eStep.passwordBefore,
    passwordAfter: passwordInputs.length,
    inheritChecked: inheritSwitch ? inheritSwitch.getAttribute('aria-checked') : null,
    inheritDisabled: inheritSwitch ? Boolean(inheritSwitch.disabled) : null,
    isolationVisible: Boolean(isolationCheckbox),
  };
})()"""

_AGENT_BOUND_JS = """((agentId) => {
  const bound = window.__myrmChatStore?.getState?.()?.agentConfig?.agentId ?? null;
  return { ready: bound === agentId, bound };
})"""


def _wait_web_fetch_escalation_persisted(api_base: str, *, timeout_sec: float = 20.0) -> dict[str, object]:
    """Wait until debounced ConfigSyncManager writes all edited fields to the backend DB."""
    deadline = time.monotonic() + timeout_sec
    last_val: object = None
    while time.monotonic() < deadline:
        val = fetch_config_value("webFetchEscalation", api_url=api_base)
        last_val = val
        fc = val.get("firecrawl") if isinstance(val, dict) else None
        if (
            isinstance(val, dict)
            and (val.get("jina_api_key") or val.get("jinaApiKey")) == "jina-e2e-live-key"
            and isinstance(fc, dict)
            and (fc.get("api_base") or fc.get("apiBase")) == "https://fc.e2e.example"
            and (fc.get("inherit_from_search") is False or fc.get("inheritFromSearch") is False)
            and fc.get("api_key") == "fc-e2e-dedicated-key"
        ):
            return val
        time.sleep(0.5)
    raise AssertionError(f"webFetchEscalation did not persist expected fields within {timeout_sec}s; last={last_val!r}")


def _completed_web_fetch_steps(chat_id: str, api_url: str) -> list[str]:
    steps: list[str] = []
    for message in stored_messages(chat_id, api_url):
        if message.get("role") != "assistant":
            continue
        metadata = message.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("completionStatus") != "complete":
            continue
        recorded = metadata.get("progressSteps")
        if not isinstance(recorded, list):
            continue
        for step in recorded:
            key = step.get("step_key") if isinstance(step, dict) else None
            if isinstance(key, str) and "web_fetch" in key and not key.endswith("_error"):
                steps.append(key)
    return steps


def _wait_live_web_fetch_turn(chat_id: str, api_url: str, *, timeout_sec: float) -> tuple[list[str], str]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        steps = _completed_web_fetch_steps(chat_id, api_url)
        replies = completed_assistant_replies(stored_messages(chat_id, api_url))
        if steps and replies:
            return steps, replies[-1]
        time.sleep(2.0)
    raise AssertionError(
        f"no completed web_fetch_tool turn within {timeout_sec}s; messages={stored_messages(chat_id, api_url)!r}"
    )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_web_fetch_escalation_card_ui_and_persistence() -> None:
    """Lane-B: verify ToC copy hygiene, custom apiBase unlock, and concurrent debounced save to real DB."""
    api_base = get_e2e_api_url()
    if not wait_e2e_provider_ready(api_url=api_base):
        pytest.fail("E2E backend not ready for web_fetch escalation Chrome signoff")

    backup = fetch_config_value("webFetchEscalation", api_url=api_base)
    try:
        put_config_value(
            "webFetchEscalation",
            {
                "enabled": False,
                "jinaApiKey": None,
                "firecrawl": {
                    "inheritFromSearch": True,
                    "api_key": None,
                    "apiBase": None,
                    "networkIsolationConfirmed": False,
                },
                "sessionCap": 5,
            },
            api_url=api_base,
        )
        warm_ui_route("/settings/search")
        with open_settings_subroute("/settings/search", timeout_ms=90_000) as (client, page):
            dismiss_blocking_modals(client, page)
            result = wait_for_state(
                client,
                page,
                _EXPAND_AND_EXERCISE_CARD_JS,
                timeout_sec=_warm_ui_parallel_wait_sec(120.0),
            )
            assert result.get("ready") is True, json.dumps(result, ensure_ascii=False)
            assert result.get("leakedInternalTerms") is False, result
            assert result.get("passwordBefore") == 1, result
            assert result.get("passwordAfter") == 2, result
            assert result.get("inheritChecked") == "false", result
            assert result.get("inheritDisabled") is True, result
            assert result.get("isolationVisible") is True, result

            persisted = _wait_web_fetch_escalation_persisted(api_base, timeout_sec=20.0)
            print(f"E2E_WEB_FETCH_ESCALATION_PERSISTED: {json.dumps(persisted)}", flush=True)
    finally:
        if backup:
            put_config_value("webFetchEscalation", backup, api_url=api_base)


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_web_fetch_live_agent_turn_reads_real_page(
    tmp_path: Path,
    e2e_resource_ledger: E2EResourceLedger,
) -> None:
    """Lane-C: real LLM in WebUI invokes web_fetch_tool against https://example.com/ and summarizes it."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    seed_live_e2e_providers(api_url)
    if not wait_e2e_provider_ready(timeout_sec=90.0):
        pytest.fail("Provider not ready — run ./myrm-dev ready --chrome")

    suffix = uuid.uuid4().hex[:8]
    created = http_json(
        "POST",
        f"{api_url}/api/v1/user-agents",
        body={
            "name": f"WebFetch Lane-C E2E {suffix}",
            "description": "Lane-C probe: agent fetches a live URL via web_fetch_tool",
            "system_prompt": "You are a helpful assistant. Always use web_fetch_tool when asked to fetch a URL.",
            "skill_ids": [],
            "mcp_ids": [],
            "enabled_builtin_tools": [],
        },
    )
    assert isinstance(created, dict), created
    created_data = created.get("data")
    agent_id = created_data.get("id") if isinstance(created_data, dict) else created.get("id")
    assert isinstance(agent_id, str) and agent_id, created
    e2e_resource_ledger.register("agent", agent_id)

    chat_id = f"e2ewf{suffix}"
    created_chat = http_json(
        "POST",
        f"{api_url}/api/v1/chats/",
        body={
            "chat_id": chat_id,
            "title": "WebFetch Lane-C E2E",
            "agent_id": agent_id,
            "action_mode": "agent",
            "messages": [],
        },
    )
    assert isinstance(created_chat, dict) and created_chat.get("success") is True, created_chat
    e2e_resource_ledger.register("chat", chat_id)

    probe = SkillChat(
        skill_name="web-fetch-live",
        agent_id=agent_id,
        chat_id=chat_id,
        out=tmp_path,
        log_offset=snapshot_backend_log_offset(api_url),
    )
    chat_url = f"{ui_url.rstrip('/')}{probe.ui_path}"

    def agent_bound(client: ChromeMcpClient, page: McpPage) -> None:
        applied = wait_for_state(
            client,
            page,
            f"({_AGENT_BOUND_JS})({json.dumps(agent_id)})",
            timeout_sec=90.0,
            page_url=chat_url,
        )
        assert applied.get("ready") is True, applied

    drive_chat_turn(ui_url, probe, _LIVE_FETCH_PROMPT, invoke_skill=False, before_send=agent_bound)

    steps, reply = _wait_live_web_fetch_turn(chat_id, api_url, timeout_sec=300.0)
    print(f"E2E_WEB_FETCH_STEPS[{chat_id}]: {steps}", flush=True)
    print(f"E2E_WEB_FETCH_REPLY[{chat_id}]: {reply[:400]}", flush=True)
    assert "example domain" in reply.lower(), f"expected Example Domain in reply, got: {reply!r}"


_TRIGGER_CUSTOM_BASE_VERIFY_JS = """(() => {
  const sections = Array.from(document.querySelectorAll('section'));
  const card = sections.find((el) =>
    /Remote Web Fetch Fallback|网页抓取远程兜底/i.test(el.textContent || '')
  );
  if (!card) return { ready: false, reason: 'card_missing' };

  const headerBtn = card.querySelector('button[aria-expanded]');
  if (!headerBtn) return { ready: false, reason: 'expand_btn_missing' };
  if (headerBtn.getAttribute('aria-expanded') !== 'true') {
    headerBtn.click();
    return { ready: false, reason: 'expanding_card' };
  }

  const baseInput = card.querySelector('input[placeholder="https://api.firecrawl.dev"]');
  if (!baseInput) return { ready: false, reason: 'base_input_missing' };

  const setInputValue = (el, val) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(el, val);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };

  if (baseInput.value !== 'http://127.0.0.1:3002') {
    setInputValue(baseInput, 'http://127.0.0.1:3002');
    return { ready: false, reason: 'setting_http_loopback_base' };
  }

  const pwInputs = Array.from(card.querySelectorAll('input[type="password"]'));
  if (pwInputs.length < 2) return { ready: false, reason: 'waiting_for_dedicated_key_input' };
  if (pwInputs[1].value !== 'fc-e2e-verify-key') {
    setInputValue(pwInputs[1], 'fc-e2e-verify-key');
    return { ready: false, reason: 'setting_verify_key' };
  }

  if (!window.__wfVerifyClicked) {
    const verifyBtn = Array.from(card.querySelectorAll('button')).find((el) =>
      /Test Firecrawl|测试 Firecrawl/i.test(el.textContent || '')
    );
    if (!verifyBtn || verifyBtn.disabled) return { ready: false, reason: 'verify_btn_not_ready' };
    window.__wfVerifyClicked = true;
    verifyBtn.click();
    return { ready: false, reason: 'waiting_for_verify_toast' };
  }

  const toasts = Array.from(document.querySelectorAll('[data-sonner-toast]')).map((el) =>
    (el.textContent || '').trim()
  );
  const failedToast = toasts.find((t) =>
    /Remote fetch verification failed|远程抓取验证失败/i.test(t)
  );
  if (!failedToast) return { ready: false, reason: 'waiting_for_failed_toast', toasts };
  return { ready: true, toast: failedToast, allToasts: toasts };
})()"""


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_web_fetch_escalation_verify_custom_base_rejection_toast() -> None:
    """Lane-B failure path: clicking Test Firecrawl on unconfirmed loopback apiBase hits real endpoint_policy."""
    api_base = get_e2e_api_url()
    if not wait_e2e_provider_ready(api_url=api_base):
        pytest.fail("E2E backend not ready for Firecrawl verify rejection Chrome signoff")

    backup = fetch_config_value("webFetchEscalation", api_url=api_base)
    try:
        put_config_value(
            "webFetchEscalation",
            {
                "enabled": False,
                "jinaApiKey": None,
                "firecrawl": {
                    "inheritFromSearch": True,
                    "api_key": None,
                    "apiBase": None,
                    "networkIsolationConfirmed": False,
                },
                "sessionCap": 5,
            },
            api_url=api_base,
        )
        warm_ui_route("/settings/search")
        with open_settings_subroute("/settings/search", timeout_ms=90_000) as (client, page):
            dismiss_blocking_modals(client, page)
            result = wait_for_state(
                client,
                page,
                _TRIGGER_CUSTOM_BASE_VERIFY_JS,
                timeout_sec=_warm_ui_parallel_wait_sec(120.0),
            )
            assert result.get("ready") is True, json.dumps(result, ensure_ascii=False)
            toast_text = str(result.get("toast") or "")
            print(f"E2E_FIRECRAWL_VERIFY_TOAST: {toast_text}", flush=True)
            assert "inherit" not in toast_text.lower(), f"unexpected inherit deadlock error: {toast_text!r}"
            assert any(tok in toast_text.lower() for tok in ("isolat", "confirm", "https", "network", "隔离", "确认")), (
                f"expected endpoint_policy isolation/https rejection message, got: {toast_text!r}"
            )
    finally:
        if backup:
            put_config_value("webFetchEscalation", backup, api_url=api_base)
