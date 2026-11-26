"""Chrome E2E: compressing a long chat by hand from the context strategy panel, in both shipped languages.

A user opens a chat with a long history, opens the panel behind the usage ring and presses "compress". The real model
writes the summary, which for a history of this size takes longer than the page's default request budget; the panel must
wait for the server's answer and then say how many tokens it saved, in the page's language. Pressing it again has nothing
left to compress, and the panel says so in the language the page was switched to. What the server stored is checked too:
the summary must still carry the facts the conversation was about, and the tokens it saved must be the figure the panel
showed.
"""

from __future__ import annotations

import json
import uuid

import pytest
from cdp_chat.support import get_e2e_api_url, get_e2e_ui_url, wait_e2e_provider_ready

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, prepare_e2e_ui_session, wait_for_state
from tests.support.context_compaction_e2e import (
    LONG_CHAT_ROUNDS,
    PROJECT_CODENAME,
    chat_page,
    create_dormant_chat,
    declare_context_window,
    ensure_locale,
    locale_text,
    open_chat,
    read_compaction_state,
    switch_locale,
)
from tests.support.e2e_provider_seed import seed_live_e2e_providers

# Usage the last reply recorded; the panel offers "compress" from 30 %.
_RECORDED_USAGE_PERCENT = 55.0
# The summary is written by the real model over the whole history; a slow gateway is allowed this long.
_COMPACTION_TIMEOUT_SEC = 240.0
_TOKENS_PLACEHOLDER = "{tokens}"


# Passive: every compact request goes through unchanged; its status or error and how long the page waited are kept.
_RECORD_COMPACT_REQUESTS_JS = """(() => {
  if (window.__e2eCompactRequests) return { ok: true, installed: false };
  const requests = [];
  window.__e2eCompactRequests = requests;
  const original = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (!/\\/chats\\/[^/]+\\/compact(\\?|$)/.test(url)) return original(input, init);
    const request = { status: null, error: null, ms: null };
    const started = performance.now();
    requests.push(request);
    try {
      const response = await original(input, init);
      request.status = response.status;
      return response;
    } catch (error) {
      request.error = error?.name ?? String(error);
      throw error;
    } finally {
      request.ms = Math.round(performance.now() - started);
    }
  };
  return { ok: true, installed: true };
})()"""


def _record_compact_requests(client: ChromeMcpClient, page: McpPage) -> None:
    installed = client.evaluate(page, _RECORD_COMPACT_REQUESTS_JS, timeout_sec=15.0)
    assert isinstance(installed, dict) and installed.get("ok") is True, f"request recorder not installed: {installed!r}"


def _strategy_label(locale: str, key: str) -> str:
    return locale_text(locale, "chat", "contextUsage", "strategy", key)


def _compress_button_js(locale: str) -> str:
    """JS expression for the compress button of the open strategy panel (``null`` while the panel is closed)."""
    return f"""(() => {{
    const panel = document.querySelector('[data-testid="context-usage-panel"]');
    return panel
      ? Array.from(panel.querySelectorAll('button')).find(
          (b) => (b.textContent || '').trim() === {json.dumps(_strategy_label(locale, "compressContext"))},
        ) ?? null
      : null;
  }})()"""


def _panel_ready_js(locale: str) -> str:
    """Open the strategy panel behind the ring; ready once the compress button of this language is there and enabled."""
    return f"""(() => {{
  const ring = document.querySelector('[data-testid="context-usage-indicator"]');
  const panel = document.querySelector('[data-testid="context-usage-panel"]');
  if (ring && !panel) ring.click();
  const button = {_compress_button_js(locale)};
  return {{
    ready: !!button && !button.disabled && document.documentElement.lang.startsWith({json.dumps(locale)}),
    hasRing: !!ring,
    hasPanel: !!panel,
    lang: document.documentElement.lang,
    buttons: panel ? Array.from(panel.querySelectorAll('button')).map((b) => (b.textContent || '').trim()) : [],
  }};
}})()"""


def _press_compress_js(locale: str) -> str:
    """Press compress once; the flag keeps a re-poll from pressing it again while the model is working."""
    return f"""(() => {{
  const button = {_compress_button_js(locale)};
  if (button && !button.disabled && !window.__e2eCompressPressed) {{
    window.__e2eCompressPressed = true;
    button.click();
  }}
  return {{ ready: window.__e2eCompressPressed === true }};
}})()"""


def _outcome_js(*, head: str, tail: str) -> str:
    """Read the line under the compress button: ready once ``head`` is shown; ``shown`` is what sits up to ``tail``."""
    return f"""(() => {{
  const panel = document.querySelector('[data-testid="context-usage-panel"]');
  const text = panel ? panel.innerText : '';
  const head = {json.dumps(head)};
  const tail = {json.dumps(tail)};
  const start = text.indexOf(head);
  let shown = null;
  if (start >= 0) {{
    const from = start + head.length;
    const end = tail ? text.indexOf(tail, from) : from;
    if (end >= from) shown = text.slice(from, end).trim();
  }}
  return {{ ready: shown !== null, shown, panelText: text.slice(-400), requests: window.__e2eCompactRequests ?? null }};
}})()"""


def _tokens_from_shown(shown: str) -> float:
    """Tokens a panel line states (``12.6K`` reads as 12600; the panel rounds to a tenth of a thousand)."""
    multiplier = {"K": 1_000, "M": 1_000_000}.get(shown[-1], 1)
    return float(shown.rstrip("KM")) * multiplier


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_manual_compaction_from_strategy_panel_with_live_model() -> None:
    """Compress a long chat from the panel in zh, then press it again in en: saved tokens first, "nothing left" after."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    endpoints = seed_live_e2e_providers(api_url)
    declare_context_window(api_url, endpoints)
    if not wait_e2e_provider_ready(timeout_sec=90.0):
        pytest.fail("Provider not ready; verify the .env.test LLM credentials")

    chat_id = f"e2emanualcompact{uuid.uuid4().hex[:8]}"
    create_dormant_chat(api_url, chat_id, context_usage_percent=_RECORDED_USAGE_PERCENT, rounds=LONG_CHAT_ROUNDS)
    before = read_compaction_state(api_url, chat_id)
    assert before["summary_persisted"] is False and before["tokens_saved"] == 0, before

    with chat_page(ui_url, chat_id) as (client, page, page_url):
        # Chinese: the real compaction.
        ensure_locale(client, page, "zh", page_url=page_url)
        open_chat(client, page, chat_id, page_url)
        _record_compact_requests(client, page)
        wait_for_state(client, page, _panel_ready_js("zh"), timeout_sec=90.0, page_url=page_url)
        wait_for_state(client, page, _press_compress_js("zh"), timeout_sec=30.0, page_url=page_url)
        head, tail = locale_text("zh", "chat", "contextUsage", "strategy", "compressSuccess").split(_TOKENS_PLACEHOLDER)
        outcome = wait_for_state(
            client, page, _outcome_js(head=head, tail=tail), timeout_sec=_COMPACTION_TIMEOUT_SEC, page_url=page_url
        )
        shown = str(outcome["shown"])
        compaction_request = outcome["requests"]

        stored = read_compaction_state(api_url, chat_id)
        assert stored["summary_persisted"] is True, stored
        saved = stored["tokens_saved"]
        assert isinstance(saved, int) and saved > 0, stored
        assert abs(_tokens_from_shown(shown) - saved) <= 60, f"panel shows {shown!r}, server stored {saved}"
        # The summary is what the model reads from now on: the project's codename must have survived it.
        assert PROJECT_CODENAME in str(stored["summary"]), f"codename lost from summary: {str(stored['summary'])[:600]}"

        # English: nothing is left to compress, and the panel says so in the language of the page.
        switch_locale(client, page, "en", page_url=page_url)
        open_chat(client, page, chat_id, page_url)
        _record_compact_requests(client, page)
        wait_for_state(client, page, _panel_ready_js("en"), timeout_sec=90.0, page_url=page_url)
        wait_for_state(client, page, _press_compress_js("en"), timeout_sec=30.0, page_url=page_url)
        nothing_en = locale_text("en", "chat", "compact", "skip", "nothing")
        said = wait_for_state(client, page, _outcome_js(head=nothing_en, tail=""), timeout_sec=90.0, page_url=page_url)
        assert locale_text("zh", "chat", "compact", "skip", "nothing") not in str(said["panelText"]), said

    after = read_compaction_state(api_url, chat_id)
    assert after["tokens_saved"] == saved, f"a second press with nothing left changed the saved total: {after}"
    print(f"E2E_MANUAL_COMPACTION chat={chat_id} saved={saved} panel_zh={shown!r} panel_en={nothing_en!r}")
    print(f"E2E_MANUAL_COMPACTION compact_requests_zh={compaction_request} compact_requests_en={said['requests']}")
