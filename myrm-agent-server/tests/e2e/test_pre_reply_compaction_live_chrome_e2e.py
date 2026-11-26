"""Chrome E2E: a chat that went quiet for hours is compacted before the model answers, and the answer survives it.

A user comes back to a long chat after three hours and asks about something said at its start. The agent is set to
compact a chat that has been quiet for half an hour, so the real model writes a summary of the history first, the page
shows the compaction as a step of the new reply in the page's language (zh and en), and the reply then comes from the
summary. The turn's own message must not count as activity: it is stored a moment before the gate looks, and the chat
has to be judged by the message before it. The step arrives before the reply exists, so the page has to open the reply
for it.
"""

from __future__ import annotations

import uuid

import pytest
from cdp_chat.support import get_e2e_api_url, get_e2e_ui_url

from tests.support.chrome_mcp_e2e import wait_for_state
from tests.support.context_compaction_e2e import (
    PROJECT_CODENAME,
    chat_page,
    create_dormant_chat,
    ensure_locale,
    locale_text,
    open_chat,
    prepare_live_model,
    read_compaction_state,
)
from tests.support.context_compaction_turn import ask_in_chat, idle_compacting_agent, record_compaction_phases, steps_panel_js

_PROMPTS = {
    "zh": "我们的数据中心迁移项目，代号和迁移窗口分别是什么？请只用一句话回答。",
    "en": "What are the codename and the migration window of our data center migration project? Answer in one sentence.",
}


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.parametrize("locale", ["zh", "en"])
def test_chat_quiet_for_hours_is_compacted_before_the_reply_with_live_model(locale: str) -> None:
    """Ask about the start of a three-hour-old chat: the compaction step shows first, then an answer from the summary."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    with idle_compacting_agent(api_url) as agent_id:
        chat_id = f"e2eprereplycompact{uuid.uuid4().hex[:8]}"
        create_dormant_chat(api_url, chat_id, agent_id=agent_id)
        before = read_compaction_state(api_url, chat_id)
        assert before["summary_persisted"] is False and before["tokens_saved"] == 0, before

        with chat_page(ui_url, chat_id) as (client, page, page_url):
            ensure_locale(client, page, locale, page_url=page_url)
            open_chat(client, page, chat_id, page_url, agent_id=agent_id)
            record_compaction_phases(client, page)

            seen = ask_in_chat(client, page, page_url, chat_id=chat_id, api_url=api_url, prompt=_PROMPTS[locale])
            # The step opens when the compaction starts and closes on exactly one phase.
            assert seen.phases[:2] == ["active", "completed"], f"compaction phases on the stream: {seen.phases}"
            assert seen.compaction_steps == [("context_compaction_completed", "complete")], f"steps on the reply: {seen.steps}"

            title = locale_text(locale, "progressSteps", "context_compaction_completed")
            wait_for_state(client, page, steps_panel_js(locale, title), timeout_sec=60.0, page_url=page_url)

        stored = read_compaction_state(api_url, chat_id)
        assert stored["summary_persisted"] is True, f"the quiet chat was not compacted: {stored}"
        saved = stored["tokens_saved"]
        assert isinstance(saved, int) and saved > 0, stored
        assert PROJECT_CODENAME in str(stored["summary"]), f"codename lost from summary: {str(stored['summary'])[:600]}"
        assert PROJECT_CODENAME.lower() in seen.reply.lower(), f"the reply does not carry the codename: {seen.reply!r}"
        print(
            f"E2E_PRE_REPLY_COMPACTION locale={locale} chat={chat_id} agent={agent_id} phases={seen.phases} steps={seen.step_keys}"
        )
        print(f"E2E_PRE_REPLY_COMPACTION saved={saved} reply={seen.reply!r}")
