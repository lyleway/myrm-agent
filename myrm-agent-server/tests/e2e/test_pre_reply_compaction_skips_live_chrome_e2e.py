"""Chrome E2E: the idle gate leaves alone what is not due for compaction, so no reply waits for a summary nobody needed.

Two users have the same long chat and the same agent (compact a chat that has been quiet for half an hour) and ask the
same kind of question. The one who wrote the chat's last message two minutes ago is answered at once: no compaction step
on the reply, nothing stored, and the model reads the history itself. The other came back after hours and is compacted
(see the pre-reply test); the question he asks next, a moment later, finds the chat active again and is answered without
a second compaction: the stored summary is the first one and the saved total stays what it was.
"""

from __future__ import annotations

import uuid

import pytest
from cdp_chat.support import get_e2e_api_url, get_e2e_ui_url

from tests.support.context_compaction_e2e import (
    PROJECT_CODENAME,
    RECENT_FOR,
    chat_page,
    create_dormant_chat,
    ensure_locale,
    open_chat,
    prepare_live_model,
    read_compaction_state,
)
from tests.support.context_compaction_turn import ask_in_chat, idle_compacting_agent, record_compaction_phases

_FIRST_QUESTION_ZH = "我们的数据中心迁移项目，代号和迁移窗口分别是什么？请只用一句话回答。"
_FIRST_QUESTION_EN = (
    "What are the codename and the migration window of our data center migration project? Answer in one sentence."
)
_FOLLOW_UP_EN = "And what was the project codename again? Answer in one sentence."


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_chat_still_in_use_is_answered_without_compaction_with_live_model() -> None:
    """Ask in a long chat whose last message is two minutes old: the reply carries no compaction step and nothing is stored."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    with idle_compacting_agent(api_url) as agent_id:
        chat_id = f"e2eprereplyrecent{uuid.uuid4().hex[:8]}"
        create_dormant_chat(api_url, chat_id, agent_id=agent_id, quiet_for=RECENT_FOR)

        with chat_page(ui_url, chat_id) as (client, page, page_url):
            ensure_locale(client, page, "zh", page_url=page_url)
            open_chat(client, page, chat_id, page_url, agent_id=agent_id)
            record_compaction_phases(client, page)

            seen = ask_in_chat(client, page, page_url, chat_id=chat_id, api_url=api_url, prompt=_FIRST_QUESTION_ZH)

        assert seen.compaction_steps == [], f"a chat in use was compacted before the reply: {seen.steps}"
        stored = read_compaction_state(api_url, chat_id)
        assert stored["summary_persisted"] is False and stored["tokens_saved"] == 0, f"the server compacted it anyway: {stored}"
        # Nothing was summarised, so the model answered from the history itself.
        assert PROJECT_CODENAME.lower() in seen.reply.lower(), f"the reply does not carry the codename: {seen.reply!r}"
        print(f"E2E_PRE_REPLY_SKIP recent chat={chat_id} phases={seen.phases} steps={seen.step_keys} reply={seen.reply!r}")


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_next_question_after_a_compaction_is_answered_without_compacting_again_with_live_model() -> None:
    """Compact a three-hour-old chat with the first question; the second one, right after, leaves the summary as it is."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    with idle_compacting_agent(api_url) as agent_id:
        chat_id = f"e2eprereplyonce{uuid.uuid4().hex[:8]}"
        create_dormant_chat(api_url, chat_id, agent_id=agent_id)

        with chat_page(ui_url, chat_id) as (client, page, page_url):
            ensure_locale(client, page, "en", page_url=page_url)
            open_chat(client, page, chat_id, page_url, agent_id=agent_id)
            record_compaction_phases(client, page)

            first = ask_in_chat(client, page, page_url, chat_id=chat_id, api_url=api_url, prompt=_FIRST_QUESTION_EN)
            assert first.compaction_steps == [("context_compaction_completed", "complete")], (
                f"steps on the first reply: {first.steps}"
            )
            after_first = read_compaction_state(api_url, chat_id)
            assert after_first["summary_persisted"] is True, f"the quiet chat was not compacted: {after_first}"

            second = ask_in_chat(client, page, page_url, chat_id=chat_id, api_url=api_url, prompt=_FOLLOW_UP_EN)

        assert second.compaction_steps == [], f"the follow-up compacted the chat again: {second.steps}"
        after_second = read_compaction_state(api_url, chat_id)
        assert after_second == after_first, (
            f"the follow-up changed what the first compaction stored: {after_first} -> {after_second}"
        )
        assert PROJECT_CODENAME.lower() in second.reply.lower(), (
            f"the follow-up reply does not carry the codename: {second.reply!r}"
        )
        print(
            f"E2E_PRE_REPLY_SKIP once chat={chat_id} saved={after_first['tokens_saved']} "
            f"first_phases={first.phases} second_steps={second.step_keys} reply={second.reply!r}"
        )
