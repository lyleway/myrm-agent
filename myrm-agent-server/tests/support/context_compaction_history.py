"""Changing the history of a compacted chat in the Chrome E2Es: compress it, then rewind, edit or regenerate the way a user does.

[INPUT]
- cdp_chat.support (POS: the messages the runtime under test stored for a chat)
- tests.support.chrome_mcp_e2e (POS: CDP page helpers, wait_for_state)
- tests.support.context_compaction_e2e (POS: the dormant long chat, the locale files, the compaction state of the server)
- tests.support.context_compaction_ui (POS: the compress control of the strategy panel, the compact requests the page sends)

[OUTPUT]
- compress_in_chat() / compress_open_chat() / compact_open_chat_by_command(): opens a dormant chat (or takes the open one) and compresses it from the strategy panel, or by typing `/compact`, with the real model; returns what the server and the page then hold
- reload_chat(): the page reloaded on the chat, with its summary shown
- listed_messages() / stored_ids(): the messages the page lists (it loads only the newest part of a long chat) and the ids the server holds, oldest first
- refresh_nonce() / history_settled_js(): the page's count of summary re-reads and the state that says a history change has been absorbed
- rewind_to_user_message() / edit_user_message() / regenerate_latest_answer() / switch_to_previous_version(): the four ways a user rewrites the history, driven through the page

[POS]
Every one of these changes the stored history, and the server decides in the same transaction whether the summary it
saved still describes it. The page learns of the verdict by re-reading the summary, so a test waits for that re-read
(the page counts them) before it compares the page with the server.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import pytest
from cdp_chat.support import fetch_chat_messages

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, reload_mcp_page, wait_for_state
from tests.support.context_compaction_e2e import (
    PROJECT_CODENAME,
    SHORT_CHAT_ROUNDS,
    create_dormant_chat,
    ensure_locale,
    locale_text,
    open_chat,
    read_compaction_state,
)
from tests.support.context_compaction_ui import (
    forget_compact_reminder,
    outcome_js,
    panel_ready_js,
    press_compress_js,
    press_enter,
    read_compact_requests,
    rearm_compress_js,
    record_compact_requests,
    type_in_composer,
    wait_for_compact_answers,
    wait_for_composer,
    wait_for_palette_choice,
)

# The usage the last reply recorded; the panel offers "compress" from 30 %.
RECORDED_USAGE_PERCENT = 55.0
# The summary is written by the real model over the whole history; a slow gateway is allowed this long.
COMPACTION_TIMEOUT_SEC = 240.0
_TOKENS_PLACEHOLDER = "{tokens}"
# One more press after a gateway stall; a second stall in a row is a failure to report, not to hide.
_MAX_COMPRESS_ATTEMPTS = 2

SummaryExpectation = Literal["gone", "kept"]

_SUMMARY_IN_PAGE_JS = """(() => {
  const summary = window.__myrmChatStore?.getState?.()?.compactedSummary ?? null;
  return { ready: typeof summary === 'string' && summary.length > 0, length: summary ? summary.length : 0 };
})()"""

_PAGE_MESSAGES_JS = """(() => ({
  ready: true,
  messages: (window.__myrmChatStore?.getState?.()?.messages ?? []).map((m) => ({ id: m.messageId, role: m.role })),
}))()"""

_REFRESH_NONCE_JS = "(() => ({ ready: true, nonce: window.__myrmChatStore?.getState?.()?.compactionRefreshNonce ?? 0 }))()"

_HISTORY_SETTLED_JS = """(() => {
  const state = window.__myrmChatStore?.getState?.();
  const turns = window.__MYRM_E2E_CHAT__?.turnSnapshot?.() ?? null;
  const summary = state?.compactedSummary ?? null;
  const summaryOk = __EXPECT__ === 'gone' ? summary === null : typeof summary === 'string' && summary.length > 0;
  return {
    ready: __DIALOG_CLOSED__ && (state?.compactionRefreshNonce ?? 0) > __REFRESH_AFTER__ && summaryOk && turns?.isStreaming === false,
    nonce: state?.compactionRefreshNonce ?? null,
    summaryChars: summary ? summary.length : 0,
    isStreaming: turns?.isStreaming ?? null,
    userCount: turns?.userCount ?? null,
    dialogOpen: !!document.querySelector('[role="dialog"]'),
    dialogText: (document.querySelector('[role="dialog"]')?.innerText ?? '').slice(0, 300),
  };
})()"""


@dataclass(frozen=True)
class PageMessage:
    id: str
    role: str


@dataclass(frozen=True)
class CompactedChat:
    """A chat the real model has summarized: what the server stored and how long the summary in the page is."""

    chat_id: str
    state: dict[str, object]
    summary_chars: int

    @property
    def summary(self) -> str:
        return str(self.state["summary"])

    @property
    def tokens_saved(self) -> object:
        return self.state["tokens_saved"]


def _press_until_summarized(client: ChromeMcpClient, page: McpPage, page_url: str) -> None:
    """Press "compress" until the server has summarized the chat.

    The server declines with reason ``timeout`` (HTTP 200, nothing stored) when the model gateway sends no token for
    its inactivity limit, which happens while other sessions load the same gateway; the user presses again, so does this.
    Any other decline is a failure.
    """
    sent_before = len(read_compact_requests(client, page))
    for attempt in range(1, _MAX_COMPRESS_ATTEMPTS + 1):
        client.evaluate(page, rearm_compress_js(), timeout_sec=15.0)
        wait_for_state(client, page, press_compress_js("en"), timeout_sec=30.0, page_url=page_url)
        wait_for_compact_answers(client, page, page_url, sent_before + attempt, timeout_sec=COMPACTION_TIMEOUT_SEC)
        answer = read_compact_requests(client, page)[-1]
        if answer.compacted:
            return
        assert answer.status == 200 and answer.reason == "timeout", f"the compaction was declined for another reason: {answer}"
        wait_for_state(client, page, panel_ready_js("en"), timeout_sec=60.0, page_url=page_url)
    pytest.fail(f"the model stalled on all {_MAX_COMPRESS_ATTEMPTS} compression attempts: {answer}")


def compress_in_chat(client: ChromeMcpClient, page: McpPage, page_url: str, api_url: str, chat_id: str) -> CompactedChat:
    """Create a dormant six-round chat, open it and compress it from its strategy panel with the real model."""
    create_dormant_chat(api_url, chat_id, context_usage_percent=RECORDED_USAGE_PERCENT, rounds=SHORT_CHAT_ROUNDS)
    assert read_compaction_state(api_url, chat_id)["summary_persisted"] is False
    ensure_locale(client, page, "en", page_url=page_url)
    open_chat(client, page, chat_id, page_url)
    return compress_open_chat(client, page, page_url, api_url, chat_id)


def compress_open_chat(client: ChromeMcpClient, page: McpPage, page_url: str, api_url: str, chat_id: str) -> CompactedChat:
    """Press "compress" in the strategy panel of the open chat and wait for the real model's summary to be stored and shown."""
    wait_for_state(client, page, panel_ready_js("en"), timeout_sec=90.0, page_url=page_url)
    record_compact_requests(client, page)
    _press_until_summarized(client, page, page_url)
    head, tail = locale_text("en", "chat", "contextUsage", "strategy", "compressSuccess").split(_TOKENS_PLACEHOLDER)
    wait_for_state(client, page, outcome_js(head=head, tail=tail), timeout_sec=COMPACTION_TIMEOUT_SEC, page_url=page_url)

    return _stored_compaction(client, page, page_url, api_url, chat_id)


def _stored_compaction(client: ChromeMcpClient, page: McpPage, page_url: str, api_url: str, chat_id: str) -> CompactedChat:
    """What the server stored for the compaction that just ended, and the summary the page now shows."""
    state = read_compaction_state(api_url, chat_id)
    assert state["summary_persisted"] is True, state
    assert PROJECT_CODENAME in str(state["summary"]), f"codename lost from summary: {str(state['summary'])[:600]}"
    in_page = wait_for_state(client, page, _SUMMARY_IN_PAGE_JS, timeout_sec=60.0, page_url=page_url)
    return CompactedChat(chat_id=chat_id, state=state, summary_chars=int(str(in_page["length"])))


def compact_open_chat_by_command(
    client: ChromeMcpClient, page: McpPage, page_url: str, api_url: str, chat_id: str
) -> CompactedChat:
    """Type ``/compact`` in the open chat and press Enter on the highlighted command; the real model writes the summary.

    The strategy panel offers "compress" only from 30 % of the window, and a chat that has just been answered afresh
    is below it, while the typed command is not gated by usage.
    """
    forget_compact_reminder(client, page)
    record_compact_requests(client, page)
    sent_before = len(read_compact_requests(client, page))
    for attempt in range(1, _MAX_COMPRESS_ATTEMPTS + 1):
        type_in_composer(client, page, page_url, "/compact")
        wait_for_palette_choice(client, page, page_url, "/compact")
        press_enter(client, page)
        wait_for_compact_answers(client, page, page_url, sent_before + attempt, timeout_sec=COMPACTION_TIMEOUT_SEC)
        wait_for_composer(client, page, page_url, "")
        answer = read_compact_requests(client, page)[-1]
        if answer.compacted:
            break
        assert answer.status == 200 and answer.reason == "timeout", f"the compaction was declined for another reason: {answer}"
    else:
        pytest.fail(f"the model stalled on all {_MAX_COMPRESS_ATTEMPTS} compression attempts: {answer}")
    return _stored_compaction(client, page, page_url, api_url, chat_id)


def reload_chat(client: ChromeMcpClient, page: McpPage, page_url: str, chat_id: str) -> None:
    """Reload the page on the chat, as a user does to see what the server holds; ready once the chat and its summary are shown.

    A freshly regenerated answer offers its version switcher only after the chat is loaded from the server.
    """
    reload_mcp_page(client, page, target_url=page_url, timeout_ms=90_000)
    open_chat(client, page, chat_id, page_url)
    wait_for_state(client, page, _SUMMARY_IN_PAGE_JS, timeout_sec=60.0, page_url=page_url)


def listed_messages(client: ChromeMcpClient, page: McpPage, page_url: str) -> list[PageMessage]:
    """The messages the page lists now, oldest first."""
    seen = wait_for_state(client, page, _PAGE_MESSAGES_JS, timeout_sec=20.0, page_url=page_url)["messages"]
    assert isinstance(seen, list)
    return [PageMessage(id=str(row["id"]), role=str(row["role"])) for row in seen if isinstance(row, dict)]


def stored_ids(chat_id: str, api_url: str) -> list[str]:
    """The ids of the messages the server holds for the chat, oldest first."""
    return [str(row["messageId"]) for row in fetch_chat_messages(chat_id, api_url=api_url) if isinstance(row, dict)]


def refresh_nonce(client: ChromeMcpClient, page: McpPage, page_url: str) -> int:
    """How many times the page has re-read the chat's compaction state."""
    return int(str(wait_for_state(client, page, _REFRESH_NONCE_JS, timeout_sec=20.0, page_url=page_url)["nonce"]))


def history_settled_js(
    *, refresh_after: int, summary: SummaryExpectation, user_count: int | None = None, dialog_closed: bool = True
) -> str:
    """Ready once the page re-read the summary after ``refresh_after`` re-reads, shows what the server decided, and is idle.

    With ``user_count`` the page must also list that many user messages; ``dialog_closed`` also waits for the dialog a
    rewind leaves open until it is done (a regenerate or an edit opens none).
    """
    script = (
        _HISTORY_SETTLED_JS.replace("__EXPECT__", json.dumps(summary))
        .replace("__REFRESH_AFTER__", str(refresh_after))
        .replace("__DIALOG_CLOSED__", "!document.querySelector('[role=\"dialog\"]')" if dialog_closed else "true")
    )
    if user_count is not None:
        script = script.replace("summaryOk &&", f"summaryOk && turns?.userCount === {user_count} &&")
    return script


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


def rewind_to_user_message(client: ChromeMcpClient, page: McpPage, page_url: str, user_index: int) -> None:
    """Rewind the conversation (not the files) to before the ``user_index``-th user message the page lists, as the dialog offers it."""
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


def _edit_user_message_js(message_id: str, edit_label: str, submit_label: str, new_text: str) -> str:
    """Open the editor of one user bubble, put ``new_text`` in it and press its submit button, each step once; ready once pressed."""
    return f"""(() => {{
  // The list row and the bubble inside it both carry the id; the bubble's own wrapper is the `group` that holds its buttons.
  const group = Array.from(document.querySelectorAll('[data-message-id=' + {json.dumps(json.dumps(message_id))} + ']'))
    .map((el) => el.closest('.group') ?? el.querySelector('.group'))
    .find(Boolean);
  if (!group) return {{ ready: false, why: 'bubble not on screen' }};
  const textarea = group.querySelector('textarea');
  if (!textarea) {{
    const open = Array.from(group.querySelectorAll('button')).find((b) => b.title === {json.dumps(edit_label)} && !b.disabled);
    if (open && !window.__e2eEditOpened) {{
      window.__e2eEditOpened = true;
      open.click();
    }}
    return {{ ready: false, why: 'editor not open yet' }};
  }}
  if (textarea.value !== {json.dumps(new_text)}) {{
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(textarea, {json.dumps(new_text)});
    textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
    return {{ ready: false, why: 'text typed' }};
  }}
  const submit = Array.from(group.querySelectorAll('button')).find((b) => (b.textContent || '').trim() === {json.dumps(submit_label)});
  if (submit && !submit.disabled && !window.__e2eEditSubmitted) {{
    window.__e2eEditSubmitted = true;
    submit.click();
  }}
  return {{ ready: window.__e2eEditSubmitted === true, why: 'submit pressed' }};
}})()"""


def edit_user_message(client: ChromeMcpClient, page: McpPage, page_url: str, message_id: str, new_text: str) -> None:
    """Edit one user message and send it again, the way the pencil and its Submit button do."""
    wait_for_state(
        client,
        page,
        _edit_user_message_js(
            message_id, locale_text("en", "chat", "message", "edit"), locale_text("en", "chat", "editSubmit"), new_text
        ),
        timeout_sec=60.0,
        page_url=page_url,
    )


_REGENERATE_JS = """(() => {
  const buttons = Array.from(document.querySelectorAll('[data-testid="message-regenerate-button"]')).filter((b) => !b.disabled);
  if (buttons.length === 1 && !window.__e2eRegenerated) {
    window.__e2eRegenerated = true;
    buttons[0].click();
  }
  return { ready: window.__e2eRegenerated === true, buttons: buttons.length };
})()"""


def regenerate_latest_answer(client: ChromeMcpClient, page: McpPage, page_url: str) -> None:
    """Press "regenerate" under the latest answer (the only one that offers it)."""
    wait_for_state(client, page, _REGENERATE_JS, timeout_sec=60.0, page_url=page_url)


def _previous_version_js(label: str) -> str:
    return f"""(() => {{
  const buttons = Array.from(document.querySelectorAll('button[aria-label={json.dumps(label)}]')).filter((b) => !b.disabled);
  if (buttons.length === 1 && !window.__e2eSiblingSwitched) {{
    window.__e2eSiblingSwitched = true;
    buttons[0].click();
  }}
  return {{ ready: window.__e2eSiblingSwitched === true, buttons: buttons.length }};
}})()"""


def switch_to_previous_version(client: ChromeMcpClient, page: McpPage, page_url: str) -> None:
    """Press the left arrow of the version switcher a regenerated answer shows ("1/2" becomes the earlier version)."""
    wait_for_state(
        client, page, _previous_version_js(locale_text("en", "common", "previous")), timeout_sec=60.0, page_url=page_url
    )
