"""Chrome LIVE E2E: a folder whose name ends in a space is granted as itself, not as the folder next to it.

Real business flow, no mocks and no API shortcut for the chat turn itself:

    user asks the live model to read a file in a folder outside the workspace -> the run parks behind the approval card
    -> user presses Approve -> the grant covers the folder of the file as named -> the model reads it and answers with its secret.

The folder is named ``shared `` (trailing space) and sits beside ``shared``, which holds another secret nobody approved. A text
trimmed on its way (the page, the API or the grant planner) turns the grant into the folder beside it, and the file the user
asked for is then not covered.

The same chat's grant API, which the desktop folder drop and the grant bar call, is then driven as they do: a blank path is
refused before it reaches the chat, the folder is granted as itself and revoked as itself.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_folder_grant_chrome_e2e.py::<test_name>
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, get_e2e_api_url, get_e2e_ui_url, http_json
from tests.support.chrome_skill_hooks_approval import approve_cards_until_reply, wait_for_approval_card
from tests.support.chrome_skill_hooks_composer import drive_chat_turn
from tests.support.chrome_skill_hooks_live_e2e import setup_file_read_chat
from tests.support.e2e_runtime_guard import E2EResourceLedger

_SYSTEM_PROMPT = (
    "You read files with the file read tool when the user asks. Call it once with exactly the path the user gives, keeping "
    "every character of it, spaces included, then reply with the secret token the file holds, on one line."
)


def _roots(response: object) -> list[str]:
    data = response.get("data") if isinstance(response, dict) else None
    roots = data.get("session_access_roots") if isinstance(data, dict) else None
    assert isinstance(roots, list), response
    return [str(root["path"]) for root in roots if isinstance(root, dict)]


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_folder_named_with_a_trailing_space_is_granted_as_itself(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    token = uuid.uuid4().hex[:8]
    spaced, twin = tmp_path / "shared ", tmp_path / "shared"
    spaced.mkdir()
    twin.mkdir()
    secret, other_secret = f"GRANTED-{token}", f"NEVER-APPROVED-{token}"
    (spaced / "secret.txt").write_text(secret, encoding="utf-8")
    (twin / "secret.txt").write_text(other_secret, encoding="utf-8")

    api_url = get_e2e_api_url()
    probe = setup_file_read_chat(tmp_path, e2e_resource_ledger, "e2egrant", _SYSTEM_PROMPT)
    request = (
        f"Read the file at this exact path with the file read tool, the folder name ends in a space: `{spaced / 'secret.txt'}`"
    )
    replies: list[str] = []

    def approve_and_read_the_reply(client: ChromeMcpClient, page: McpPage) -> None:
        wait_for_approval_card(client, page)
        replies.append(approve_cards_until_reply(client, page, probe, secret, timeout_sec=240.0, tolerate_closing_note=True))

    drive_chat_turn(get_e2e_ui_url(), probe, request, invoke_skill=False, while_open=approve_and_read_the_reply)

    assert secret in replies[0], replies
    assert other_secret not in replies[0], "the reply holds the secret of the folder nobody approved"

    roots_url = f"{api_url}/api/v1/chats/{probe.chat_id}/session-access-roots"
    http_json("POST", roots_url, body={"path": "   ", "writable": False}, expected_statuses=frozenset({422}))
    granted = _roots(http_json("POST", roots_url, body={"path": str(spaced), "writable": False}))
    assert str(spaced) in granted and str(twin) not in granted, granted
    revoked = _roots(http_json("PATCH", roots_url, body={"path": str(spaced)}))
    assert str(spaced) not in revoked, revoked
