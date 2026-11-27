"""Chrome E2E: the link under an IM reply's web hand-off button opens the stored conversation in the WebUI.

The link comes from the function the router builds the button with (``build_web_chat_url``), on the WebUI origin of
the run, for a chat the server stored with a user message and an assistant reply. The browser has to land on that
conversation and render the stored exchange.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from app.remote_access.mobile_deep_link import build_web_chat_url
from tests.support.chrome_mcp_e2e import (
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    open_mcp_page,
    prepare_e2e_ui_session,
    warm_ui_route,
)

_SEED_ROUTE = "/api/v1/chats/test/seed-chat-share-fixture"
_TEXT_WAIT_MS = 90_000


def _wait_for_conversation_js(user_text: str, reply_start: str) -> str:
    """Resolve once the message rows of the conversation view hold both texts; the result names the path reached.

    Only ``[data-message-id]`` rows count. A page that did not open the chat can still echo it elsewhere (the
    catch-up inbox card shows the question and a reply preview), so a whole-page text match passes on a wrong route.
    """
    return f"""(async () => {{
  const needles = {json.dumps([user_text, reply_start])};
  const deadline = Date.now() + {_TEXT_WAIT_MS};
  const rowTexts = () => Array.from(document.querySelectorAll('[data-message-id]')).map((row) => row.innerText || '');
  while (Date.now() < deadline) {{
    const rows = rowTexts();
    if (needles.every((needle) => rows.some((text) => text.includes(needle)))) {{
      return {{ ok: true, path: location.pathname, rows: rows.length }};
    }}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }}
  const page = document.body ? document.body.innerText : '';
  return {{ ok: false, path: location.pathname, rows: rowTexts().map((text) => text.slice(0, 120)), page: page.slice(0, 800) }};
}})()"""


@pytest.mark.chrome_e2e(
    execution_mode="SHARED",
    access_scope="NAMESPACE_WRITE",
    workload="STANDARD",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_the_hand_off_link_opens_the_stored_conversation() -> None:
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    seeded = http_json("POST", f"{api_url}{_SEED_ROUTE}")
    assert isinstance(seeded, dict), seeded
    chat_id = str(seeded["chat_id"])

    link = build_web_chat_url(chat_id=chat_id, base_url=ui_url)
    assert link, "the link builder returned no link for a stored chat"
    path = urlsplit(link).path
    assert path == f"/{chat_id}", link

    warm_ui_route(path)
    # The first words of the seeded reply: the page renders markdown, so the full sentence is not compared.
    reply_start = " ".join(str(seeded["assistant_text"]).split()[:6])
    with open_mcp_page(link, timeout_ms=120_000) as (client, page):
        landed = client.evaluate(page, _wait_for_conversation_js(str(seeded["user_text"]), reply_start), timeout_sec=120.0)
    assert isinstance(landed, dict), landed
    assert landed.get("ok") is True, json.dumps(landed, ensure_ascii=False)
    assert landed.get("path") == path, landed
