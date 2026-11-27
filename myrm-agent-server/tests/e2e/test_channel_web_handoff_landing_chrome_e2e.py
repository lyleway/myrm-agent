"""Chrome E2E: the link under an IM reply's web hand-off button opens the IM conversation in the WebUI.

The chat is stored the way an IM turn stores it (a channel session key, a source named after the channel, a title cut
from the user text). The link comes from the function the router builds the button with (``build_web_chat_url``), on
the WebUI origin of the run. The browser has to land on that conversation and render the stored exchange.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from app.remote_access.mobile_deep_link import build_web_chat_url
from tests.support.chrome_conversation_rows import conversation_rows_hold_js
from tests.support.chrome_mcp_e2e import (
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    open_mcp_page,
    prepare_e2e_ui_session,
    warm_ui_route,
)

_SEED_ROUTE = "/api/v1/chats/test/seed-channel-chat-fixture"


@pytest.mark.chrome_e2e(
    execution_mode="SHARED",
    access_scope="NAMESPACE_WRITE",
    workload="STANDARD",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_the_hand_off_link_opens_the_conversation_an_im_turn_was_stored_in() -> None:
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    seeded = http_json("POST", f"{api_url}{_SEED_ROUTE}")
    assert isinstance(seeded, dict), seeded
    chat_id = str(seeded["chat_id"])
    assert seeded["source"] != "web", "the seeded chat is not an IM chat"

    link = build_web_chat_url(chat_id=chat_id, base_url=ui_url)
    assert link, "the link builder returned no link for a stored chat"
    path = urlsplit(link).path
    assert path == f"/{chat_id}", link

    warm_ui_route(path)
    # The first words of the seeded reply: the page renders markdown, so the full sentence is not compared.
    reply_start = " ".join(str(seeded["assistant_text"]).split()[:6])
    with open_mcp_page(link, timeout_ms=120_000) as (client, page):
        landed = client.evaluate(page, conversation_rows_hold_js([str(seeded["user_text"]), reply_start]), timeout_sec=120.0)
    assert isinstance(landed, dict), landed
    assert landed.get("ok") is True, json.dumps(landed, ensure_ascii=False)
    assert landed.get("path") == path, landed
