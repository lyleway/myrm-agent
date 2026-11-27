"""Chrome E2E: rewinding a compacted chat drops the summary the rewind made stale, on the server and in the page.

A user compresses a long chat from the strategy panel (the real model writes the summary), then rewinds to an earlier
turn in the Rewind dialog. The summary covered turns the rewind has just deleted, so the server must not keep it for the
next prompt and the page must stop showing it, while the turns before the rewind point stay and the running total of
tokens the compaction saved is left as it was.
"""

from __future__ import annotations

import json
import uuid

import pytest
from cdp_chat.support import chat_user_message_count, fetch_chat_messages, get_e2e_api_url, get_e2e_ui_url

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, wait_for_state
from tests.support.context_compaction_e2e import (
    PROJECT_CODENAME,
    SHORT_CHAT_ROUNDS,
    chat_page,
    create_dormant_chat,
    ensure_locale,
    locale_text,
    open_chat,
    prepare_live_model,
    read_compaction_state,
)
from tests.support.context_compaction_ui import outcome_js, panel_ready_js, press_compress_js

# The usage the last reply recorded; the panel offers "compress" from 30 %.
_RECORDED_USAGE_PERCENT = 55.0
# The summary is written by the real model over the whole history; a slow gateway is allowed this long.
_COMPACTION_TIMEOUT_SEC = 240.0
# The rewind target is the fourth user message the page lists.
_REWIND_TO_USER_INDEX = 3
_TOKENS_PLACEHOLDER = "{tokens}"

_SUMMARY_IN_PAGE_JS = """(() => {
  const summary = window.__myrmChatStore?.getState?.()?.compactedSummary ?? null;
  return { ready: typeof summary === 'string' && summary.length > 0, length: summary ? summary.length : 0 };
})()"""

_PAGE_MESSAGE_IDS_JS = """(() => ({
  ready: true,
  ids: (window.__myrmChatStore?.getState?.()?.messages ?? []).map((m) => ({ id: m.messageId, role: m.role })),
}))()"""

_SUMMARY_GONE_AFTER_REWIND_JS = """(() => {
  const state = window.__myrmChatStore?.getState?.();
  const turns = window.__MYRM_E2E_CHAT__?.turnSnapshot?.() ?? null;
  return {
    ready: !document.querySelector('[role="dialog"]') && (state?.compactedSummary ?? null) === null && turns?.userCount === __USER_COUNT__,
    summary: state?.compactedSummary ?? null,
    userCount: turns?.userCount ?? null,
    dialogOpen: !!document.querySelector('[role="dialog"]'),
  };
})()"""


def _open_rewind_dialog_js(button_label: str, user_index: int) -> str:
    """Click the rewind button of the user message at ``user_index`` once; ready when the dialog is open."""
    return f"""(() => {{
  const buttons = Array.from(
    document.querySelectorAll('[data-testid="rewind-message-button"], [aria-label={json.dumps(button_label)}]'),
  ).filter((b) => b.isConnected && !b.disabled);
  const button = buttons[{user_index}];
  if (button && !window.__e2eRewindOpened) {{
    window.__e2eRewindOpened = true;
    button.scrollIntoView({{ block: 'center' }});
    button.dispatchEvent(new PointerEvent('pointerdown', {{ bubbles: true }}));
    button.dispatchEvent(new PointerEvent('pointerup', {{ bubbles: true }}));
    button.click();
  }}
  return {{ ready: !!document.querySelector('[role="dialog"]'), buttons: buttons.length }};
}})()"""


def _press_in_dialog_js(label: str) -> str:
    """Click the dialog button that shows ``label`` once (a re-poll must not press it again)."""
    return f"""(() => {{
  const dialog = document.querySelector('[role="dialog"]');
  const button = Array.from(dialog?.querySelectorAll('button') ?? []).find((b) => (b.textContent || '').includes({json.dumps(label)}));
  const key = '__e2ePressed_' + {json.dumps(label)};
  if (button && !window[key]) {{
    window[key] = true;
    button.dispatchEvent(new PointerEvent('pointerdown', {{ bubbles: true }}));
    button.dispatchEvent(new PointerEvent('pointerup', {{ bubbles: true }}));
    button.click();
  }}
  return {{ ready: window[key] === true, buttons: Array.from(dialog?.querySelectorAll('button') ?? []).map((b) => (b.textContent || '').trim()) }};
}})()"""


def _rewind_to_user_message(client: ChromeMcpClient, page: McpPage, page_url: str, user_index: int) -> None:
    """Rewind the conversation (not the files) to before the user message at ``user_index``, the way the dialog offers it."""
    wait_for_state(
        client,
        page,
        _open_rewind_dialog_js(locale_text("en", "chat", "rewind", "buttonLabel"), user_index),
        timeout_sec=60.0,
        page_url=page_url,
    )
    for key in ("scopeConversation", "confirm"):
        wait_for_state(
            client, page, _press_in_dialog_js(locale_text("en", "chat", "rewind", key)), timeout_sec=30.0, page_url=page_url
        )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_rewind_drops_the_summary_written_after_it() -> None:
    """Compress a long chat with the real model, rewind to the fourth user message the page lists: the summary is gone from the server and the page."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = f"e2erewindcompact{uuid.uuid4().hex[:8]}"
    create_dormant_chat(api_url, chat_id, context_usage_percent=_RECORDED_USAGE_PERCENT, rounds=SHORT_CHAT_ROUNDS)
    assert read_compaction_state(api_url, chat_id)["summary_persisted"] is False

    with chat_page(ui_url, chat_id) as (client, page, page_url):
        ensure_locale(client, page, "en", page_url=page_url)
        open_chat(client, page, chat_id, page_url)
        wait_for_state(client, page, panel_ready_js("en"), timeout_sec=90.0, page_url=page_url)
        wait_for_state(client, page, press_compress_js("en"), timeout_sec=30.0, page_url=page_url)
        head, tail = locale_text("en", "chat", "contextUsage", "strategy", "compressSuccess").split(_TOKENS_PLACEHOLDER)
        wait_for_state(client, page, outcome_js(head=head, tail=tail), timeout_sec=_COMPACTION_TIMEOUT_SEC, page_url=page_url)

        compacted = read_compaction_state(api_url, chat_id)
        assert compacted["summary_persisted"] is True, compacted
        assert PROJECT_CODENAME in str(compacted["summary"]), f"codename lost from summary: {str(compacted['summary'])[:600]}"
        in_page = wait_for_state(client, page, _SUMMARY_IN_PAGE_JS, timeout_sec=60.0, page_url=page_url)
        assert chat_user_message_count(chat_id, api_url=api_url) == SHORT_CHAT_ROUNDS

        listed = wait_for_state(client, page, _PAGE_MESSAGE_IDS_JS, timeout_sec=20.0, page_url=page_url)["ids"]
        listed_users = [row["id"] for row in listed if row["role"] == "user"]
        rewound_id = listed_users[_REWIND_TO_USER_INDEX]
        stored_ids = [row.get("messageId") for row in fetch_chat_messages(chat_id, api_url=api_url) if isinstance(row, dict)]
        assert rewound_id in stored_ids, f"the page lists {rewound_id}, the server holds {stored_ids}"
        expected_kept = stored_ids[: stored_ids.index(rewound_id)]

        _rewind_to_user_message(client, page, page_url, _REWIND_TO_USER_INDEX)
        settled = wait_for_state(
            client,
            page,
            _SUMMARY_GONE_AFTER_REWIND_JS.replace("__USER_COUNT__", str(_REWIND_TO_USER_INDEX)),
            timeout_sec=90.0,
            page_url=page_url,
        )

    after = read_compaction_state(api_url, chat_id)
    assert after["summary_persisted"] is False and after["summary"] == "", f"the rewound chat kept its summary: {after}"
    assert after["tokens_saved"] == compacted["tokens_saved"], f"the running total of saved tokens changed: {after}"
    kept = [row.get("messageId") for row in fetch_chat_messages(chat_id, api_url=api_url) if isinstance(row, dict)]
    assert kept == expected_kept, f"rewound {rewound_id}: the server kept {kept}, expected {expected_kept}"
    print(
        f"E2E_COMPACTION_REWIND chat={chat_id} summary_chars_before={in_page['length']} "
        f"rewound={rewound_id} kept_rows={len(kept)} tokens_saved={after['tokens_saved']} "
        f"user_turns_after={settled['userCount']} summary_after={after['summary']!r}"
    )
