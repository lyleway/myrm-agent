"""Chrome E2E: rewinding a compacted chat drops the summary the rewind made stale, on the server and in the page.

A user compresses a long chat from the strategy panel (the real model writes the summary), then rewinds to an earlier
turn in the Rewind dialog. The summary covered turns the rewind has just deleted, so the server must not keep it for the
next prompt and the page must stop showing it, while the turns before the rewind point stay and the running total of
tokens the compaction saved is left as it was.
"""

from __future__ import annotations

import uuid

import pytest
from cdp_chat.support import chat_user_message_count, get_e2e_api_url, get_e2e_ui_url

from tests.support.chrome_mcp_e2e import wait_for_state
from tests.support.context_compaction_e2e import SHORT_CHAT_ROUNDS, chat_page, prepare_live_model, read_compaction_state
from tests.support.context_compaction_history import (
    compress_in_chat,
    history_settled_js,
    listed_messages,
    refresh_nonce,
    rewind_to_user_message,
    stored_ids,
)

# The rewind target is the fourth user message the page lists.
_REWIND_TO_USER_INDEX = 3


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
    with chat_page(ui_url, chat_id) as (client, page, page_url):
        compacted = compress_in_chat(client, page, page_url, api_url, chat_id)
        assert chat_user_message_count(chat_id, api_url=api_url) == SHORT_CHAT_ROUNDS

        listed_users = [message.id for message in listed_messages(client, page, page_url) if message.role == "user"]
        rewound_id = listed_users[_REWIND_TO_USER_INDEX]
        stored = stored_ids(chat_id, api_url)
        assert rewound_id in stored, f"the page lists {rewound_id}, the server holds {stored}"
        expected_kept = stored[: stored.index(rewound_id)]

        nonce = refresh_nonce(client, page, page_url)
        rewind_to_user_message(client, page, page_url, _REWIND_TO_USER_INDEX)
        settled = wait_for_state(
            client,
            page,
            history_settled_js(refresh_after=nonce, summary="gone", user_count=_REWIND_TO_USER_INDEX),
            timeout_sec=90.0,
            page_url=page_url,
        )

    after = read_compaction_state(api_url, chat_id)
    assert after["summary_persisted"] is False and after["summary"] == "", f"the rewound chat kept its summary: {after}"
    assert after["tokens_saved"] == compacted.tokens_saved, f"the running total of saved tokens changed: {after}"
    kept = stored_ids(chat_id, api_url)
    assert kept == expected_kept, f"rewound {rewound_id}: the server kept {kept}, expected {expected_kept}"
    print(
        f"E2E_COMPACTION_REWIND chat={chat_id} summary_chars_before={compacted.summary_chars} "
        f"rewound={rewound_id} kept_rows={len(kept)} tokens_saved={after['tokens_saved']} "
        f"user_turns_after={settled['userCount']} summary_after={after['summary']!r}"
    )
