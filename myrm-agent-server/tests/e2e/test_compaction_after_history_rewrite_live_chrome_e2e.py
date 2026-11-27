"""Chrome E2E: the ways a user rewrites a compacted chat, each through the real page and the real model.

A user compresses a long chat from the strategy panel, then changes its history. Whether the summary survives is
decided by what the change touched: a rewind of a turn made after the compression leaves the summary alone, while a
regenerate or an edit-and-resend of a turn the summary covers makes the server drop it, and the page stops showing it
once it has re-read the chat. Switching back to an earlier version of an answer drops a summary written after that
version, because the summary describes the other version. A rewound chat still answers, from the turns that remain.
"""

from __future__ import annotations

import uuid

import pytest
from cdp_chat.support import fetch_chat_messages, get_e2e_api_url, get_e2e_ui_url

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, wait_for_state
from tests.support.context_compaction_e2e import PROJECT_CODENAME, chat_page, prepare_live_model, read_compaction_state
from tests.support.context_compaction_history import (
    compact_open_chat_by_command,
    compress_in_chat,
    edit_user_message,
    history_settled_js,
    listed_messages,
    refresh_nonce,
    regenerate_latest_answer,
    reload_chat,
    rewind_to_user_message,
    stored_ids,
    switch_to_previous_version,
)
from tests.support.context_compaction_turn import ask_in_chat, completed_replies, record_compaction_phases, wait_new_reply_stored

_ASK_CODENAME = "上面讨论的项目代号是什么？只回答代号本身。"
_EDITED_QUESTION = "这是编辑后的问题：项目代号是什么？只回答代号本身。"
# The fourth user message the page lists (the page shows only the newest ten of the twelve messages).
_TARGET_USER_INDEX = 3


def _chat_id() -> str:
    return f"e2ehistory{uuid.uuid4().hex[:8]}"


def _stored_rows(chat_id: str, api_url: str) -> list[dict[str, object]]:
    return [row for row in fetch_chat_messages(chat_id, api_url=api_url) if isinstance(row, dict)]


def _wait_summary_dropped(
    client: ChromeMcpClient, page: McpPage, page_url: str, nonce: int, *, timeout_sec: float
) -> dict[str, object]:
    """Wait until the page has re-read the chat after ``nonce`` re-reads, shows no summary and is not streaming.

    A regenerate or an edit starts a real turn right after the server drops the summary, so unlike a rewind it is not
    done when the page is idle: the caller also waits for the reply the turn stores.
    """
    return wait_for_state(
        client,
        page,
        history_settled_js(refresh_after=nonce, summary="gone", dialog_closed=False),
        timeout_sec=timeout_sec,
        page_url=page_url,
    )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_rewind_of_a_turn_made_after_the_compression_keeps_the_summary() -> None:
    """Compress, ask one more real question, rewind exactly that turn: the summary and everything before the turn stay."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = _chat_id()
    with chat_page(ui_url, chat_id) as (client, page, page_url):
        compacted = compress_in_chat(client, page, page_url, api_url, chat_id)
        before_turn = stored_ids(chat_id, api_url)
        record_compaction_phases(client, page)
        ask_in_chat(client, page, page_url, chat_id=chat_id, api_url=api_url, prompt=_ASK_CODENAME)
        with_turn = stored_ids(chat_id, api_url)
        assert with_turn[: len(before_turn)] == before_turn and len(with_turn) == len(before_turn) + 2, with_turn

        users = [message for message in listed_messages(client, page, page_url) if message.role == "user"]
        nonce = refresh_nonce(client, page, page_url)
        rewind_to_user_message(client, page, page_url, len(users) - 1)
        settled = wait_for_state(
            client, page, history_settled_js(refresh_after=nonce, summary="kept"), timeout_sec=90.0, page_url=page_url
        )

    after = read_compaction_state(api_url, chat_id)
    assert after["summary_persisted"] is True and after["summary"] == compacted.summary, f"the summary changed: {after}"
    assert after["tokens_saved"] == compacted.tokens_saved, f"the running total of saved tokens changed: {after}"
    kept = stored_ids(chat_id, api_url)
    assert kept == before_turn, f"the server kept {kept}, expected the {len(before_turn)} rows from before the turn"
    print(
        f"E2E_COMPACTION_KEEP chat={chat_id} summary_chars={compacted.summary_chars} page_summary_chars={settled['summaryChars']} "
        f"rows_before_turn={len(before_turn)} rows_with_turn={len(with_turn)} rows_after_rewind={len(kept)}"
    )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_rewound_chat_answers_from_the_turns_that_remain() -> None:
    """Compress, rewind to the fourth user message, ask the real model: it answers from the first turn and no summary comes back."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = _chat_id()
    with chat_page(ui_url, chat_id) as (client, page, page_url):
        compacted = compress_in_chat(client, page, page_url, api_url, chat_id)
        nonce = refresh_nonce(client, page, page_url)
        rewind_to_user_message(client, page, page_url, _TARGET_USER_INDEX)
        wait_for_state(client, page, history_settled_js(refresh_after=nonce, summary="gone"), timeout_sec=90.0, page_url=page_url)
        kept = stored_ids(chat_id, api_url)

        record_compaction_phases(client, page)
        turn = ask_in_chat(client, page, page_url, chat_id=chat_id, api_url=api_url, prompt=_ASK_CODENAME)

    assert PROJECT_CODENAME in turn.reply, f"the reply did not name the project: {turn.reply[:400]!r}"
    after = read_compaction_state(api_url, chat_id)
    assert after["summary_persisted"] is False and after["summary"] == "", f"a summary came back after the rewind: {after}"
    assert after["tokens_saved"] == compacted.tokens_saved, f"the running total of saved tokens changed: {after}"
    answered = stored_ids(chat_id, api_url)
    assert answered[: len(kept)] == kept and len(answered) == len(kept) + 2, f"kept {kept}, then {answered}"
    print(
        f"E2E_COMPACTION_REWIND_THEN_ASK chat={chat_id} rows_kept={len(kept)} rows_after_turn={len(answered)} reply={turn.reply[:80]!r}"
    )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_regenerating_the_last_answer_of_a_compacted_chat_drops_the_summary() -> None:
    """Compress, press regenerate under the last answer: the real model answers again and the summary is gone."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = _chat_id()
    with chat_page(ui_url, chat_id) as (client, page, page_url):
        compacted = compress_in_chat(client, page, page_url, api_url, chat_id)
        before = stored_ids(chat_id, api_url)
        completed_before = completed_replies(chat_id, api_url)
        nonce = refresh_nonce(client, page, page_url)
        regenerate_latest_answer(client, page, page_url)
        dropped = _wait_summary_dropped(client, page, page_url, nonce, timeout_sec=90.0)
        print(f"E2E_DIALOG_AFTER_SUMMARY_DROP open={dropped['dialogOpen']} text={dropped['dialogText']!r}")
        wait_new_reply_stored(chat_id, api_url, completed_before=completed_before)
        _wait_summary_dropped(client, page, page_url, nonce, timeout_sec=120.0)

    after = read_compaction_state(api_url, chat_id)
    assert after["summary_persisted"] is False and after["summary"] == "", f"the regenerated chat kept its summary: {after}"
    assert after["tokens_saved"] == compacted.tokens_saved, f"the running total of saved tokens changed: {after}"
    answered = stored_ids(chat_id, api_url)
    assert answered[:-1] == before[:-1] and answered[-1] != before[-1], f"before {before}, after regenerate {answered}"
    print(f"E2E_COMPACTION_REGENERATE chat={chat_id} replaced={before[-1]} by={answered[-1]} rows={len(answered)}")


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_editing_and_resending_an_earlier_message_of_a_compacted_chat_drops_the_summary() -> None:
    """Compress, edit the fourth user message and resend it: the turns after it are replaced, the summary is gone, the model answers."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = _chat_id()
    with chat_page(ui_url, chat_id) as (client, page, page_url):
        compacted = compress_in_chat(client, page, page_url, api_url, chat_id)
        before = stored_ids(chat_id, api_url)
        edited_id = [message.id for message in listed_messages(client, page, page_url) if message.role == "user"][
            _TARGET_USER_INDEX
        ]
        assert edited_id in before, f"the page lists {edited_id}, the server holds {before}"
        completed_before = completed_replies(chat_id, api_url)
        nonce = refresh_nonce(client, page, page_url)
        edit_user_message(client, page, page_url, edited_id, _EDITED_QUESTION)
        dropped = _wait_summary_dropped(client, page, page_url, nonce, timeout_sec=90.0)
        print(f"E2E_DIALOG_AFTER_SUMMARY_DROP open={dropped['dialogOpen']} text={dropped['dialogText']!r}")
        wait_new_reply_stored(chat_id, api_url, completed_before=completed_before)
        _wait_summary_dropped(client, page, page_url, nonce, timeout_sec=120.0)

    after = read_compaction_state(api_url, chat_id)
    assert after["summary_persisted"] is False and after["summary"] == "", f"the edited chat kept its summary: {after}"
    assert after["tokens_saved"] == compacted.tokens_saved, f"the running total of saved tokens changed: {after}"
    rows = _stored_rows(chat_id, api_url)
    kept = before[: before.index(edited_id)]
    assert [row["messageId"] for row in rows[: len(kept)]] == kept and len(rows) == len(kept) + 2, f"kept {kept}, then {rows}"
    asked, answer = rows[len(kept)], rows[len(kept) + 1]
    assert asked["role"] == "user" and _EDITED_QUESTION in str(asked["content"]), asked
    assert answer["role"] == "assistant" and PROJECT_CODENAME in str(answer["content"]), (
        f"the reply did not name the project: {answer}"
    )
    print(
        f"E2E_COMPACTION_EDIT chat={chat_id} edited={edited_id} rows_kept={len(kept)} rows_now={len(rows)} reply={str(answer['content'])[:80]!r}"
    )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_switching_to_an_earlier_version_of_an_answer_drops_a_summary_written_after_it() -> None:
    """Regenerate, compact again with ``/compact``, step back to the earlier version of the answer: that summary is gone too."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = _chat_id()
    with chat_page(ui_url, chat_id) as (client, page, page_url):
        compress_in_chat(client, page, page_url, api_url, chat_id)
        original = stored_ids(chat_id, api_url)
        completed_before = completed_replies(chat_id, api_url)
        nonce = refresh_nonce(client, page, page_url)
        regenerate_latest_answer(client, page, page_url)
        _wait_summary_dropped(client, page, page_url, nonce, timeout_sec=90.0)
        wait_new_reply_stored(chat_id, api_url, completed_before=completed_before)
        _wait_summary_dropped(client, page, page_url, nonce, timeout_sec=120.0)
        regenerated = stored_ids(chat_id, api_url)
        assert regenerated[:-1] == original[:-1] and regenerated[-1] != original[-1], f"{original} then {regenerated}"

        recompressed = compact_open_chat_by_command(client, page, page_url, api_url, chat_id)
        reload_chat(client, page, page_url, chat_id)
        # The switch re-loads the chat, which is how the page learns the summary is gone: no re-read is counted.
        switch_to_previous_version(client, page, page_url)
        wait_for_state(
            client,
            page,
            history_settled_js(refresh_after=-1, summary="gone", dialog_closed=False),
            timeout_sec=90.0,
            page_url=page_url,
        )

    after = read_compaction_state(api_url, chat_id)
    assert after["summary_persisted"] is False and after["summary"] == "", f"the switched chat kept its summary: {after}"
    assert after["tokens_saved"] == recompressed.tokens_saved, f"the running total of saved tokens changed: {after}"
    switched = stored_ids(chat_id, api_url)
    assert switched == original, f"the earlier version is not the active one again: {switched}, expected {original}"
    print(
        f"E2E_COMPACTION_SWITCH chat={chat_id} regenerated={regenerated[-1]} back_to={switched[-1]} "
        f"summary_chars_before_switch={recompressed.summary_chars} tokens_saved={after['tokens_saved']}"
    )
