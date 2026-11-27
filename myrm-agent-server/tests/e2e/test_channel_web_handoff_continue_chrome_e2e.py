"""Chrome LIVE E2E: a question asked on the page a web hand-off link opens is answered in the same chat.

The chat is the one an IM turn stored (a channel session key, a source named after the channel) under an agent that is
not the default one, opened by the link the router builds under the IM reply's button: by its path alone, with no agent
in the address. The page has to load the chat and bind its agent by itself: that is checked before the test bridge
attaches anything, for the attach would cover for a route that did not open the chat. The user types a question in the
composer, the live model answers, and the exchange lands in that chat after the IM one, with the whole conversation on
the page and the chat still the agent's.

Formal run::

    ./myrm-dev test --detach -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_channel_web_handoff_continue_chrome_e2e.py
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pytest

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cdp_chat.support import (  # noqa: E402
    dump_backend_log_on_failure,
    snapshot_backend_log_offset,
    wait_e2e_provider_ready,
)

from app.remote_access.mobile_deep_link import build_web_chat_url  # noqa: E402
from tests.support.chrome_conversation_rows import conversation_rows_hold_js  # noqa: E402
from tests.support.chrome_mcp_e2e import (  # noqa: E402
    ChromeMcpClient,
    McpPage,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    prepare_e2e_ui_session,
    wait_for_state,
)
from tests.support.chrome_skill_hooks_composer import describe_page, drive_chat_turn  # noqa: E402
from tests.support.chrome_skill_hooks_live_e2e import SkillChat  # noqa: E402
from tests.support.chrome_skill_hooks_observe import (  # noqa: E402
    transcript_text,
    wait_completed_assistant_reply_containing,
)
from tests.support.e2e_provider_seed import seed_live_e2e_providers  # noqa: E402
from tests.support.e2e_runtime_guard import E2EResourceLedger  # noqa: E402

_SEED_ROUTE = "/api/v1/chats/test/seed-channel-chat-fixture"

# What the page made of the address on its own: the chat loaded under its route, and the chat's agent bound (a message
# sent before that runs on the default agent).
_ROUTE_STATE_JS = """((chatId, agentId) => {
  const state = window.__myrmChatStore?.getState?.();
  const bound = state?.agentConfig?.agentId ?? null;
  const loaded = state?.chatId === chatId && Boolean(state?.isMessagesLoaded) && !state?.notFound && !state?.loadError;
  return {
    ready: loaded && bound === agentId,
    chatId: state?.chatId ?? null,
    bound,
    loaded: Boolean(state?.isMessagesLoaded),
    notFound: Boolean(state?.notFound),
    loadError: Boolean(state?.loadError),
  };
})"""


def _create_agent(api_url: str, suffix: str) -> str:
    """A custom agent without tools: not the default agent, so the page has to bind it from the chat."""
    created = http_json(
        "POST",
        f"{api_url}/api/v1/user-agents",
        body={
            "name": f"Hand-off continue E2E {suffix}",
            "description": "Agent of the IM conversation a hand-off link opens",
            "skill_ids": [],
            "mcp_ids": [],
            "enabled_builtin_tools": [],
        },
    )
    data = created.get("data") if isinstance(created, dict) else None
    agent_id = data.get("id") if isinstance(data, dict) else None
    assert isinstance(agent_id, str) and agent_id, created
    return agent_id


@dataclass(frozen=True)
class _LinkedChat(SkillChat):
    """A chat opened the way the hand-off link opens it: by the path of the link alone."""

    link: str

    @property
    def ui_path(self) -> str:
        return urlsplit(self.link).path


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_question_asked_on_the_page_the_hand_off_link_opens_is_answered_in_the_same_chat(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    seed_live_e2e_providers(api_url)
    if not wait_e2e_provider_ready(timeout_sec=90.0):
        pytest.fail("Provider not ready — run ./myrm-dev ready --chrome")

    agent_id = _create_agent(api_url, uuid.uuid4().hex[:8])
    e2e_resource_ledger.register("agent", agent_id)
    seeded = http_json("POST", f"{api_url}{_SEED_ROUTE}?{urlencode({'agent_id': agent_id})}")
    assert isinstance(seeded, dict), seeded
    chat_id = str(seeded["chat_id"])
    e2e_resource_ledger.register("chat", chat_id)
    assert seeded["agent_id"] == agent_id, seeded
    im_question, im_answer = str(seeded["user_text"]), str(seeded["assistant_text"])
    assert seeded["source"] != "web", "the seeded chat is not an IM chat"

    link = build_web_chat_url(chat_id=chat_id, base_url=ui_url)
    assert link, "the link builder returned no link for a stored chat"
    probe = _LinkedChat(
        skill_name="",
        agent_id=agent_id,
        chat_id=chat_id,
        out=tmp_path,
        log_offset=snapshot_backend_log_offset(api_url),
        link=link,
    )
    assert f"{ui_url.rstrip('/')}{probe.ui_path}" == link, "the page is not opened by the hand-off link itself"

    token = f"WEBCONT-{uuid.uuid4().hex[:8]}"
    question = f"Reply with exactly this single token and nothing else: {token}"
    im_answer_start = " ".join(im_answer.split()[:6])  # the page renders markdown, so the sentence is not compared whole

    def page_opened_the_chat_by_its_route(client: ChromeMcpClient, page: McpPage) -> None:
        script = f"({_ROUTE_STATE_JS})({json.dumps(chat_id)}, {json.dumps(agent_id)})"
        wait_for_state(client, page, script, timeout_sec=120.0, page_url=link)

    def page_renders_the_im_conversation(client: ChromeMcpClient, page: McpPage) -> None:
        # Typing starts only after the page rendered what the IM turn stored.
        shown = client.evaluate(page, conversation_rows_hold_js([im_question, im_answer_start]), timeout_sec=120.0)
        assert isinstance(shown, dict) and shown.get("ok") is True, json.dumps(shown, ensure_ascii=False)

    def answer_lands_in_the_chat(client: ChromeMcpClient, page: McpPage) -> None:
        answer = wait_completed_assistant_reply_containing(chat_id, api_url, token, timeout_sec=240.0)
        assert token in answer, answer
        print(f"E2E_ASSISTANT_REPLY[{chat_id}]: {answer[:300]}", flush=True)
        # The chat holds the IM exchange first, then this one, and the page shows all of it.
        stored = transcript_text(chat_id, api_url)
        assert stored.index(im_question) < stored.index(im_answer) < stored.index(token), stored[:1500]
        shown = client.evaluate(page, conversation_rows_hold_js([im_question, im_answer_start, token]), timeout_sec=120.0)
        assert isinstance(shown, dict) and shown.get("ok") is True, (
            f"{json.dumps(shown, ensure_ascii=False)}\n{describe_page(client, page)}"
        )
        assert shown.get("path") == probe.ui_path, shown
        detail = http_json("GET", f"{api_url}/api/v1/chats/{chat_id}")
        assert isinstance(detail, dict) and detail["data"]["chat"]["agent_id"] == agent_id, detail

    with dump_backend_log_on_failure(api_url):
        drive_chat_turn(
            ui_url,
            probe,
            question,
            invoke_skill=False,
            after_open=page_opened_the_chat_by_its_route,
            before_send=page_renders_the_im_conversation,
            while_open=answer_lands_in_the_chat,
        )
