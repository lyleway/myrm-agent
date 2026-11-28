"""Chrome E2E: a manual compaction the server declines is worded as what it is, never as a failure, and one asked for while another runs joins it.

Three real situations, each typed or pressed the way a user does: a chat so short there is nothing to compress; a new
chat nothing has been sent in, which the server has never heard of; and a compression asked for while the real model is
still writing the first summary. The first two end with a reason and none of the three is an error, so none may show the
red "compression failed" line, and the composer is emptied as it is after any command that ran. The third is not turned
away: the second request joins the run of the first, both read the one result, and the server compacts once.
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
from tests.support.context_compaction_requests import (
    read_compact_requests,
    record_compact_requests,
    wait_for_compact_answers,
    wait_for_compact_requests_sent,
)
from tests.support.context_compaction_ui import (
    compressing_js,
    count_in,
    forget_compact_reminder,
    panel_ready_js,
    press_compress_js,
    press_enter,
    read_toasts,
    record_toasts,
    type_in_composer,
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
def test_compaction_asked_while_one_is_running_joins_it_with_live_model() -> None:
    """Press compress in the panel, then type ``/compact`` while the model is still summarising: both read the one result, zh."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = f"e2ejoincompact{uuid.uuid4().hex[:8]}"
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
        # Both starts are accepted while the model is still writing: the typed command waits for the run that is going.
        wait_for_compact_requests_sent(client, page, page_url, 2)
        while_running = read_compact_requests(client, page)

        _, values = wait_for_toast(
            client,
            page,
            page_url,
            locale_fragments("zh", "commands", "builtin", "compacted"),
            kind="success",
            timeout_sec=_COMPACTION_TIMEOUT_SEC,
        )
        shown_messages, shown_saved = (count_in(value) for value in values)
        wait_for_compact_answers(client, page, page_url, 2, timeout_sec=_COMPACTION_TIMEOUT_SEC)
        wait_for_composer(client, page, page_url, "")
        requests = read_compact_requests(client, page)
        toasts = read_toasts(client, page)

    assert len(while_running) == 2 and all(request.state is None for request in while_running), (
        f"the first was not still running when the second was sent: {while_running}"
    )
    assert [(request.status, request.error, request.state) for request in requests] == [(202, None, "succeeded")] * 2, requests
    assert requests[0].compacted and requests[1].result == requests[0].result, (
        f"the second did not read the first's run: {requests}"
    )
    assert (shown_messages, shown_saved) == (requests[1].message_count, requests[1].tokens_saved), (values, requests)
    busy = locale_text("zh", "chat", "compact", "skip", "busy")
    assert not [toast for toast in toasts if busy in toast], (
        f"a compaction that joined a running one was told it is running: {toasts}"
    )
    _assert_no_failure_toast("zh", toasts)
    stored = read_compaction_state(api_url, chat_id)
    assert stored["summary_persisted"] is True and stored["tokens_saved"] == requests[0].tokens_saved > 0, (stored, requests)
    print(
        f"E2E_COMPACT_JOIN chat={chat_id} first_ms={requests[0].ms} second_ms={requests[1].ms} saved={requests[0].tokens_saved}"
    )
    print(f"E2E_COMPACT_JOIN requests={requests} toasts={toasts}")


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

    assert [(request.status, request.state) for request in requests] == [(202, "succeeded")] and not requests[0].compacted, (
        requests
    )
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

    assert [(request.status, request.state) for request in requests] == [(202, "succeeded")] and not requests[0].compacted, (
        requests
    )
    assert requests[0].reason == "chat_not_found", requests
    _assert_no_failure_toast("zh", toasts)
    print(f"E2E_COMPACT_NEW_CHAT chat={new_chat_id} requests={requests} toasts={toasts}")
