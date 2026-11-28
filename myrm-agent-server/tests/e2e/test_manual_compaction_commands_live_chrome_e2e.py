"""Chrome E2E: compressing a chat by typing ``/compact`` in the composer, the two ways the page offers.

A bare ``/compact`` opens the slash palette with the command highlighted; Enter runs it, the page notes that a manual
compaction clears the Prompt Cache, the real model writes the summary and a toast says what it saved (zh). A command
with a topic goes through a confirmation instead: ticking "don't remind me" and confirming compacts with that topic
(en), and the next typed compaction asks nothing and says there is nothing left to compress. Whatever a toast states
must be what the server stored, and the summary must still carry the facts the conversation was about.
"""

from __future__ import annotations

import uuid

import pytest
from cdp_chat.support import get_e2e_api_url, get_e2e_ui_url

from tests.support.context_compaction_e2e import (
    PROJECT_CODENAME,
    chat_page,
    create_dormant_chat,
    ensure_locale,
    locale_fragments,
    locale_text,
    open_chat,
    prepare_live_model,
    read_compaction_state,
)
from tests.support.context_compaction_requests import read_compact_requests, record_compact_requests
from tests.support.context_compaction_ui import (
    compact_dialog_is_open,
    confirm_compact_dialog,
    count_in,
    forget_compact_reminder,
    press_enter,
    read_compact_reminder,
    read_toasts,
    record_toasts,
    tick_dont_remind,
    type_in_composer,
    wait_for_compact_dialog,
    wait_for_composer,
    wait_for_palette_choice,
    wait_for_toast,
)

# The summary is written by the real model; a slow gateway is allowed this long.
_COMPACTION_TIMEOUT_SEC = 240.0
_TOPIC = "migration window"


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_slash_compact_from_the_palette_compacts_the_chat_with_live_model() -> None:
    """Type ``/compact``, press Enter on the highlighted command: the Prompt Cache note, then what was saved, in zh."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = f"e2eslashcompact{uuid.uuid4().hex[:8]}"
    create_dormant_chat(api_url, chat_id)
    before = read_compaction_state(api_url, chat_id)
    assert before["summary_persisted"] is False and before["tokens_saved"] == 0, before

    with chat_page(ui_url, chat_id) as (client, page, page_url):
        ensure_locale(client, page, "zh", page_url=page_url)
        open_chat(client, page, chat_id, page_url)
        forget_compact_reminder(client, page)
        record_compact_requests(client, page)
        record_toasts(client, page)

        type_in_composer(client, page, page_url, "/compact")
        choice = wait_for_palette_choice(client, page, page_url, "/compact")
        press_enter(client, page)

        wait_for_toast(client, page, page_url, locale_fragments("zh", "commands", "builtin", "compactToast"), kind="info")
        _, values = wait_for_toast(
            client,
            page,
            page_url,
            locale_fragments("zh", "commands", "builtin", "compacted"),
            kind="success",
            timeout_sec=_COMPACTION_TIMEOUT_SEC,
        )
        shown_messages, shown_saved = (count_in(value) for value in values)
        wait_for_composer(client, page, page_url, "")
        requests = read_compact_requests(client, page)
        toasts = read_toasts(client, page)

    assert len(requests) == 1 and requests[0].status == 202 and requests[0].compacted, f"compact requests: {requests}"
    assert requests[0].sent is None, f"a bare /compact sent a topic: {requests[0].sent}"
    assert (shown_messages, shown_saved) == (requests[0].message_count, requests[0].tokens_saved), (values, requests)
    stored = read_compaction_state(api_url, chat_id)
    assert stored["summary_persisted"] is True, stored
    assert stored["tokens_saved"] == shown_saved > 0, f"toast said {shown_saved}, server stored {stored}"
    assert PROJECT_CODENAME in str(stored["summary"]), f"codename lost from summary: {str(stored['summary'])[:600]}"
    print(f"E2E_SLASH_COMPACT chat={chat_id} palette={choice['chosen']!r} messages={shown_messages} saved={shown_saved}")
    print(f"E2E_SLASH_COMPACT requests={requests} toasts={toasts}")


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_slash_compact_with_a_topic_asks_first_and_remembers_the_answer_with_live_model() -> None:
    """Type ``/compact <topic>``: nothing is sent until the user confirms; ticking the box makes the next one ask nothing."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = f"e2etopiccompact{uuid.uuid4().hex[:8]}"
    create_dormant_chat(api_url, chat_id)
    command = f"/compact {_TOPIC}"

    with chat_page(ui_url, chat_id) as (client, page, page_url):
        try:
            ensure_locale(client, page, "en", page_url=page_url)
            open_chat(client, page, chat_id, page_url)
            forget_compact_reminder(client, page)
            record_compact_requests(client, page)
            record_toasts(client, page)

            type_in_composer(client, page, page_url, command)
            press_enter(client, page)
            wait_for_compact_dialog(client, page, page_url, locale_text("en", "chat", "compact", "confirmTitle"))
            asked = read_compact_requests(client, page)
            assert asked == [], f"the page compacted before the user confirmed: {asked}"

            tick_dont_remind(client, page, page_url)
            confirm_compact_dialog(client, page, page_url, locale_text("en", "chat", "compact", "confirmButton"))
            wait_for_toast(client, page, page_url, locale_fragments("en", "chat", "compact", "compacting"), kind="loading")
            first_toast, values = wait_for_toast(
                client,
                page,
                page_url,
                locale_fragments("en", "chat", "compact", "success"),
                kind="success",
                timeout_sec=_COMPACTION_TIMEOUT_SEC,
            )
            shown_messages, shown_saved = (count_in(value) for value in values)
            remembered = read_compact_reminder(client, page)
            after_first = read_compaction_state(api_url, chat_id)

            # The box was ticked: the same command now asks nothing and finds nothing left to compress.
            type_in_composer(client, page, page_url, command)
            press_enter(client, page)
            nothing = locale_fragments("en", "chat", "compact", "skip", "nothing")
            wait_for_toast(client, page, page_url, nothing, kind="info")
            dialog_again = compact_dialog_is_open(client, page)
            requests = read_compact_requests(client, page)
            toasts = read_toasts(client, page)
        finally:
            # The choice lives in the browser's storage, which the next test in this browser would inherit.
            try:
                forget_compact_reminder(client, page)
            except Exception as exc:  # noqa: BLE001 - a failed cleanup must not hide why the test failed
                print(f"E2E_TOPIC_COMPACT could not clear the stored reminder choice: {exc}")

    assert remembered == "true", f"ticking the box stored {remembered!r}"
    assert dialog_again is False, "the confirmation came back although the user asked not to be reminded"
    assert [request.sent for request in requests] == [{"focus_topic": _TOPIC}] * 2, f"compact requests: {requests}"
    assert requests[0].compacted and (shown_messages, shown_saved) == (requests[0].message_count, requests[0].tokens_saved), (
        requests
    )
    assert f"({_TOPIC})" in first_toast["text"], f"the toast does not name the topic: {first_toast}"
    assert not requests[1].compacted and requests[1].reason in {"too_few_messages", "no_compactable_messages"}, requests
    assert after_first["summary_persisted"] is True and after_first["tokens_saved"] == shown_saved > 0, after_first
    assert PROJECT_CODENAME in str(after_first["summary"]), f"codename lost from summary: {str(after_first['summary'])[:600]}"
    assert read_compaction_state(api_url, chat_id) == after_first, "the second command changed what the first one stored"
    print(f"E2E_TOPIC_COMPACT chat={chat_id} messages={shown_messages} saved={shown_saved} remembered={remembered}")
    print(f"E2E_TOPIC_COMPACT requests={requests} toasts={toasts}")
