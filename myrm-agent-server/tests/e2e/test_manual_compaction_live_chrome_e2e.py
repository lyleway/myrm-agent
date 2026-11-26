"""Chrome E2E: compressing a long chat by hand from the context strategy panel, in both shipped languages.

A user opens a chat with a long history, opens the panel behind the usage ring and presses "compress". The real model
writes the summary, which for a history of this size takes longer than the page's default request budget; the panel must
wait for the server's answer and then say how many tokens it saved, in the page's language. Pressing it again has nothing
left to compress, and the panel says so in the language the page was switched to. What the server stored is checked too:
the summary must still carry the facts the conversation was about, and the tokens it saved must be the figure the panel
showed.
"""

from __future__ import annotations

import uuid

import pytest
from cdp_chat.support import get_e2e_api_url, get_e2e_ui_url

from tests.support.chrome_mcp_e2e import wait_for_state
from tests.support.context_compaction_e2e import (
    LONG_CHAT_ROUNDS,
    PROJECT_CODENAME,
    chat_page,
    create_dormant_chat,
    ensure_locale,
    locale_text,
    open_chat,
    prepare_live_model,
    read_compaction_state,
    switch_locale,
)
from tests.support.context_compaction_ui import (
    outcome_js,
    panel_ready_js,
    press_compress_js,
    read_compact_requests,
    record_compact_requests,
    tokens_from_shown,
)

# Usage the last reply recorded; the panel offers "compress" from 30 %.
_RECORDED_USAGE_PERCENT = 55.0
# The summary is written by the real model over the whole history; a slow gateway is allowed this long.
_COMPACTION_TIMEOUT_SEC = 240.0
_TOKENS_PLACEHOLDER = "{tokens}"


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
    prepare_live_model(api_url)

    chat_id = f"e2emanualcompact{uuid.uuid4().hex[:8]}"
    create_dormant_chat(api_url, chat_id, context_usage_percent=_RECORDED_USAGE_PERCENT, rounds=LONG_CHAT_ROUNDS)
    before = read_compaction_state(api_url, chat_id)
    assert before["summary_persisted"] is False and before["tokens_saved"] == 0, before

    with chat_page(ui_url, chat_id) as (client, page, page_url):
        # Chinese: the real compaction.
        ensure_locale(client, page, "zh", page_url=page_url)
        open_chat(client, page, chat_id, page_url)
        record_compact_requests(client, page)
        wait_for_state(client, page, panel_ready_js("zh"), timeout_sec=90.0, page_url=page_url)
        wait_for_state(client, page, press_compress_js("zh"), timeout_sec=30.0, page_url=page_url)
        head, tail = locale_text("zh", "chat", "contextUsage", "strategy", "compressSuccess").split(_TOKENS_PLACEHOLDER)
        outcome = wait_for_state(
            client, page, outcome_js(head=head, tail=tail), timeout_sec=_COMPACTION_TIMEOUT_SEC, page_url=page_url
        )
        shown = str(outcome["shown"])
        requests_zh = read_compact_requests(client, page)

        stored = read_compaction_state(api_url, chat_id)
        assert stored["summary_persisted"] is True, stored
        saved = stored["tokens_saved"]
        assert isinstance(saved, int) and saved > 0, stored
        assert abs(tokens_from_shown(shown) - saved) <= 60, f"panel shows {shown!r}, server stored {saved}"
        # The summary is what the model reads from now on: the project's codename must have survived it.
        assert PROJECT_CODENAME in str(stored["summary"]), f"codename lost from summary: {str(stored['summary'])[:600]}"
        assert [(request.status, request.error) for request in requests_zh] == [(200, None)], requests_zh

        # English: nothing is left to compress, and the panel says so in the language of the page.
        switch_locale(client, page, "en", page_url=page_url)
        open_chat(client, page, chat_id, page_url)
        record_compact_requests(client, page)
        wait_for_state(client, page, panel_ready_js("en"), timeout_sec=90.0, page_url=page_url)
        wait_for_state(client, page, press_compress_js("en"), timeout_sec=30.0, page_url=page_url)
        nothing_en = locale_text("en", "chat", "compact", "skip", "nothing")
        said = wait_for_state(client, page, outcome_js(head=nothing_en, tail=""), timeout_sec=90.0, page_url=page_url)
        assert locale_text("zh", "chat", "compact", "skip", "nothing") not in str(said["panelText"]), said
        requests_en = read_compact_requests(client, page)

    after = read_compaction_state(api_url, chat_id)
    assert after["tokens_saved"] == saved, f"a second press with nothing left changed the saved total: {after}"
    print(f"E2E_MANUAL_COMPACTION chat={chat_id} saved={saved} panel_zh={shown!r} panel_en={nothing_en!r}")
    print(f"E2E_MANUAL_COMPACTION compact_requests_zh={requests_zh} compact_requests_en={requests_en}")
