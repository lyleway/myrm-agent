"""Chrome E2E: a manual compaction the server declines is worded as what it is, never as a failure.

Three real situations, each typed or pressed the way a user does: a compression is already running while the user asks
for another (the real model is still writing the first summary); a chat so short there is nothing to compress; and a
new chat nothing has been sent in, which the server has never heard of. None of them is an error, so none may show the
red "compression failed" line, and the composer is emptied as it is after any command that ran.
"""

from __future__ import annotations

import uuid

import pytest
from cdp_chat.support import get_e2e_api_url, get_e2e_ui_url

from tests.support.chrome_mcp_e2e import http_json_once, wait_for_state
from tests.support.context_compaction_e2e import (
    LONG_CHAT_ROUNDS,
    chat_page,
    create_dormant_chat,
    ensure_locale,
    locale_fragments,
    locale_text,
    open_chat,
    open_new_chat,
    prepare_live_model,
    read_compaction_state,
)
from tests.support.context_compaction_ui import (
    compressing_js,
    forget_compact_reminder,
    panel_ready_js,
    press_compress_js,
    press_enter,
    read_compact_requests,
    read_toasts,
    record_compact_requests,
    record_toasts,
    type_in_composer,
    wait_for_compact_answers,
    wait_for_composer,
    wait_for_palette_choice,
    wait_for_toast,
)

# Usage the last reply recorded; the panel offers "compress" from 30 %.
_RECORDED_USAGE_PERCENT = 55.0
# The summary is written by the real model over the whole history; a slow gateway is allowed this long.
_COMPACTION_TIMEOUT_SEC = 240.0
# What the server answers when there is too little to compress.
_NOTHING_REASONS = {"too_few_messages", "no_compactable_messages"}


def _assert_no_failure_toast(locale: str, toasts: list[str]) -> None:
    failed = locale_text(locale, "chat", "compact", "failed")
    assert not [toast for toast in toasts if failed in toast or toast.startswith("error:")], (
        f"a decline was shown as a failure: {toasts}"
    )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_compaction_asked_while_one_is_running_is_told_so_with_live_model() -> None:
    """Press compress in the panel, then type ``/compact`` while the model is still summarising: "already running", zh."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = f"e2ebusycompact{uuid.uuid4().hex[:8]}"
    create_dormant_chat(api_url, chat_id, context_usage_percent=_RECORDED_USAGE_PERCENT, rounds=LONG_CHAT_ROUNDS)

    with chat_page(ui_url, chat_id) as (client, page, page_url):
        ensure_locale(client, page, "zh", page_url=page_url)
        open_chat(client, page, chat_id, page_url)
        forget_compact_reminder(client, page)
        record_compact_requests(client, page)
        record_toasts(client, page)

        wait_for_state(client, page, panel_ready_js("zh"), timeout_sec=90.0, page_url=page_url)
        wait_for_state(client, page, press_compress_js("zh"), timeout_sec=30.0, page_url=page_url)
        # The first compression is under way: the panel's own button says so and refuses a second press.
        wait_for_state(client, page, compressing_js("zh"), timeout_sec=30.0, page_url=page_url)

        type_in_composer(client, page, page_url, "/compact")
        wait_for_palette_choice(client, page, page_url, "/compact")
        press_enter(client, page)
        wait_for_toast(
            client, page, page_url, locale_fragments("zh", "chat", "compact", "skip", "busy"), kind="info", timeout_sec=60.0
        )
        wait_for_composer(client, page, page_url, "")
        while_running = read_compact_requests(client, page)

        wait_for_compact_answers(client, page, page_url, 2, timeout_sec=_COMPACTION_TIMEOUT_SEC)
        requests = read_compact_requests(client, page)
        toasts = read_toasts(client, page)

    assert len(while_running) == 2 and while_running[0].result is None, f"the first was not still running: {while_running}"
    assert [(request.status, request.error) for request in requests] == [(200, None)] * 2, requests
    assert not requests[1].compacted and requests[1].reason == "concurrent_compaction_in_progress", requests
    assert requests[0].compacted, f"the running compaction did not finish: {requests}"
    _assert_no_failure_toast("zh", toasts)
    stored = read_compaction_state(api_url, chat_id)
    assert stored["summary_persisted"] is True and stored["tokens_saved"] == requests[0].tokens_saved > 0, (stored, requests)
    print(
        f"E2E_COMPACT_BUSY chat={chat_id} busy_answered_in_ms={requests[1].ms} first_ms={requests[0].ms} saved={requests[0].tokens_saved}"
    )
    print(f"E2E_COMPACT_BUSY requests={requests} toasts={toasts}")


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_compact_in_a_chat_too_short_to_compress_says_nothing_to_compress() -> None:
    """Type ``/compact`` in a chat of one exchange: the page says there is nothing to compress, in en, and stores nothing."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = f"e2etinycompact{uuid.uuid4().hex[:8]}"
    create_dormant_chat(api_url, chat_id, rounds=1)

    with chat_page(ui_url, chat_id) as (client, page, page_url):
        ensure_locale(client, page, "en", page_url=page_url)
        open_chat(client, page, chat_id, page_url)
        forget_compact_reminder(client, page)
        record_compact_requests(client, page)
        record_toasts(client, page)

        type_in_composer(client, page, page_url, "/compact")
        wait_for_palette_choice(client, page, page_url, "/compact")
        press_enter(client, page)
        wait_for_toast(client, page, page_url, locale_fragments("en", "chat", "compact", "skip", "nothing"), kind="info")
        wait_for_composer(client, page, page_url, "")
        requests = read_compact_requests(client, page)
        toasts = read_toasts(client, page)

    assert len(requests) == 1 and requests[0].status == 200 and not requests[0].compacted, requests
    assert requests[0].reason in _NOTHING_REASONS, requests
    _assert_no_failure_toast("en", toasts)
    stored = read_compaction_state(api_url, chat_id)
    assert stored["summary_persisted"] is False and stored["tokens_saved"] == 0, stored
    print(f"E2E_COMPACT_NOTHING chat={chat_id} requests={requests} toasts={toasts}")


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_compact_in_a_new_chat_nothing_was_sent_in_says_nothing_to_compress() -> None:
    """Type ``/compact`` on the new-chat page, before sending anything: nothing to compress, in zh, not a failure."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    with chat_page(ui_url) as (client, page, page_url):
        ensure_locale(client, page, "zh", page_url=page_url)
        new_chat_id = open_new_chat(client, page, page_url)
        status, _ = http_json_once("GET", f"{api_url}/api/v1/chats/{new_chat_id}")
        assert status == 404, f"the server already knows the new chat {new_chat_id} (status {status})"
        forget_compact_reminder(client, page)
        record_compact_requests(client, page)
        record_toasts(client, page)

        type_in_composer(client, page, page_url, "/compact")
        wait_for_palette_choice(client, page, page_url, "/compact")
        press_enter(client, page)
        wait_for_toast(client, page, page_url, locale_fragments("zh", "chat", "compact", "skip", "nothing"), kind="info")
        wait_for_composer(client, page, page_url, "")
        requests = read_compact_requests(client, page)
        toasts = read_toasts(client, page)

    assert len(requests) == 1 and requests[0].status == 200 and not requests[0].compacted, requests
    assert requests[0].reason == "chat_not_found", requests
    _assert_no_failure_toast("zh", toasts)
    print(f"E2E_COMPACT_NEW_CHAT chat={new_chat_id} requests={requests} toasts={toasts}")
