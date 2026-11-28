"""Chrome LIVE E2E: a folder whose name ends in a space is granted as itself, not as the folder next to it.

Real business flow, no mocks and no API shortcut for the chat turns themselves:

    user asks the live model to read a file in a folder outside the workspace -> the run parks behind the approval card
    -> the card lists the folder to grant -> user presses Approve (with or without ticking the folder grant)
    -> the model reads the file and answers with its secret.

The folder is named ``shared `` (trailing space) and sits beside ``shared``, which holds a secret nobody approved. A text trimmed
on its way (the grant planner, the page or the API) turns the grant into the folder beside it:
  - the card lists the one folder the call opens, exactly as named: the folders sit under ``/tmp``, a prefix the gateway masks in
    every other text, and a masked path would be a folder that does not exist when the card hands it back;
  - Approve alone covers that call only; the folder grant ticked on the card keeps ``shared `` open for the chat, and a second
    folder ticked from another card joins it instead of taking its place, so a question about another file in ``shared `` parks
    behind no card, and a question about ``shared`` (the folder next to it) does;
  - the grant API the desktop folder drop and the grant bar call refuses a blank path, and grants and revokes a path as given
    (a folder deleted since is revoked as itself, and the folder above it stays held).

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_folder_grant_chrome_e2e.py::<test_name>
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, get_e2e_api_url, get_e2e_ui_url, http_json, wait_for_state
from tests.support.chrome_skill_hooks_approval import approve_cards_until_reply, wait_for_approval_card
from tests.support.chrome_skill_hooks_composer import drive_chat_turn
from tests.support.chrome_skill_hooks_live_e2e import SkillChat, setup_file_read_chat
from tests.support.chrome_skill_hooks_observe import completed_reply_containing, stored_messages
from tests.support.e2e_runtime_guard import E2EResourceLedger

_SYSTEM_PROMPT = (
    "You read files with the file read tool when the user asks. Call it once with exactly the path the user gives, keeping "
    "every character of it, spaces included, then reply with the secret token the file holds, on one line."
)

_GRANT_BOX = '[role="checkbox"][aria-labelledby$="-title"]'
# The folder grant of the card: whether it is ticked and the folders it lists, as the page shows them.
_CARD_JS = f"""(() => {{
  const box = document.querySelector('{_GRANT_BOX}');
  const panel = box ? box.closest('div.space-y-2') : null;
  const folders = panel ? Array.from(panel.querySelectorAll('li span.font-mono')).map((el) => el.textContent ?? '') : [];
  return {{ ready: Boolean(box), checked: box?.getAttribute('aria-checked') === 'true', folders }};
}})()"""
_TICKED_JS = f"""(() => {{
  const box = document.querySelector('{_GRANT_BOX}');
  return {{ ready: box?.getAttribute('aria-checked') === 'true' }};
}})()"""
_TICK_JS = f"(() => {{ document.querySelector('{_GRANT_BOX}')?.click(); return true; }})()"
_REJECT_JS = """(() => {
  const reject = Array.from(document.querySelectorAll('button')).find((b) => /Reject|拒绝/.test((b.textContent || '').trim()));
  reject?.click();
  return Boolean(reject);
})()"""
_QUEUE_LEN_JS = "(window.__MYRM_E2E_CHAT__?.toolApprovalSnapshot?.().queueLen ?? 0)"


@dataclass(frozen=True)
class _Folders:
    """``shared `` and ``shared`` side by side, each with a file whose secret only its own folder's read can show, and ``other``."""

    spaced: Path
    twin: Path
    other: Path
    secret: str
    second_secret: str
    twin_secret: str
    other_secret: str

    def request(self, file: Path) -> str:
        return f"Read the file at this exact path with the file read tool, spaces in folder names count: `{file}`"


@pytest.fixture
def short_root() -> Iterator[Path]:
    """A short folder under ``/tmp``, a prefix the outbound scrub rewrites.

    The model copies the path into its tool call, and a long pytest temp path does not survive that copy on every
    model (one slipped an underscore into it), so the card would name a folder the test never made.
    """
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="fg", dir="/tmp")))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _make_folders(root: Path) -> _Folders:
    token = uuid.uuid4().hex[:8]
    folders = _Folders(
        root / "shared ",
        root / "shared",
        root / "other",
        f"GRANTED-{token}",
        f"GRANTED-AGAIN-{token}",
        f"NEVER-APPROVED-{token}",
        f"GRANTED-OTHER-{token}",
    )
    for folder in (folders.spaced, folders.twin, folders.other):
        folder.mkdir()
    (folders.spaced / "secret.txt").write_text(folders.secret, encoding="utf-8")
    (folders.spaced / "second.txt").write_text(folders.second_secret, encoding="utf-8")
    (folders.twin / "secret.txt").write_text(folders.twin_secret, encoding="utf-8")
    (folders.other / "secret.txt").write_text(folders.other_secret, encoding="utf-8")
    return folders


def _card(client: ChromeMcpClient, page: McpPage) -> dict[str, object]:
    card = client.evaluate(page, _CARD_JS, timeout_sec=15.0)
    assert isinstance(card, dict) and card.get("ready") is True, card
    return card


def _paths(roots: object, response: object) -> list[str]:
    assert isinstance(roots, list), response
    return [str(root["path"]) for root in roots if isinstance(root, dict)]


def _roots(response: object) -> list[str]:
    """The folders a grant or revoke call answers with."""
    data = response.get("data") if isinstance(response, dict) else None
    return _paths(data.get("session_access_roots") if isinstance(data, dict) else None, response)


def _chat_roots(chat_id: str) -> list[str]:
    """The folders the stored chat holds; a chat that holds none stores null."""
    response = http_json("GET", f"{get_e2e_api_url()}/api/v1/chats/{chat_id}")
    data = response.get("data") if isinstance(response, dict) else None
    chat = data.get("chat") if isinstance(data, dict) else None
    assert isinstance(chat, dict) and "session_access_roots" in chat, response
    return _paths(chat["session_access_roots"] or [], response)


def _tick_the_folder_grant_and_approve(probe: SkillChat, folder: Path, secret: str) -> Callable[[ChromeMcpClient, McpPage], None]:
    """What the user does at the card of a read in ``folder``: tick "also grant folder access", press Approve."""

    def while_open(client: ChromeMcpClient, page: McpPage) -> None:
        wait_for_approval_card(client, page)
        assert _card(client, page)["folders"] == [str(folder)]
        assert client.evaluate(page, _TICK_JS, timeout_sec=15.0) is True
        wait_for_state(client, page, _TICKED_JS, timeout_sec=30.0)
        approve_cards_until_reply(client, page, probe, secret, timeout_sec=240.0, tolerate_closing_note=True)

    return while_open


def _wait_for_reply_without_card(client: ChromeMcpClient, page: McpPage, probe: SkillChat, token: str) -> str:
    deadline = time.monotonic() + 240.0
    while time.monotonic() < deadline:
        queued = client.evaluate(page, _QUEUE_LEN_JS, timeout_sec=15.0)
        assert queued == 0, "the run parked behind an approval card for a folder the chat already holds"
        reply = completed_reply_containing(stored_messages(probe.chat_id, get_e2e_api_url()), token)
        if reply is not None:
            return reply
        time.sleep(2.0)
    pytest.fail(f"no reply with {token} in 240 s")


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_approving_a_read_lists_and_opens_the_folder_as_named(
    tmp_path: Path, short_root: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    folders = _make_folders(short_root)
    api_url = get_e2e_api_url()
    probe = setup_file_read_chat(tmp_path, e2e_resource_ledger, "e2egrant", _SYSTEM_PROMPT)
    replies: list[str] = []

    def approve_and_read_the_reply(client: ChromeMcpClient, page: McpPage) -> None:
        wait_for_approval_card(client, page)
        assert _card(client, page)["folders"] == [str(folders.spaced)], "the card must list the one folder the call opens"
        replies.append(
            approve_cards_until_reply(client, page, probe, folders.secret, timeout_sec=240.0, tolerate_closing_note=True)
        )

    request = folders.request(folders.spaced / "secret.txt")
    drive_chat_turn(get_e2e_ui_url(), probe, request, invoke_skill=False, while_open=approve_and_read_the_reply)

    assert folders.secret in replies[0] and folders.twin_secret not in replies[0], replies
    assert _chat_roots(probe.chat_id) == [], "Approve without the folder grant must not keep a folder open for the chat"

    roots_url = f"{api_url}/api/v1/chats/{probe.chat_id}/session-access-roots"
    http_json("POST", roots_url, body={"path": "   ", "writable": False}, expected_statuses=frozenset({422}))
    granted = _roots(http_json("POST", roots_url, body={"path": str(folders.spaced), "writable": False}))
    assert granted == [str(folders.spaced)], granted
    assert _roots(http_json("PATCH", roots_url, body={"path": str(folders.spaced)})) == []

    # A held folder deleted since is revoked by the path the roots bar shows, and the folder holding it stays held.
    deleted = folders.twin / "deleted"
    deleted.mkdir()
    for held in (folders.twin, deleted):
        http_json("POST", roots_url, body={"path": str(held), "writable": False})
    deleted.rmdir()
    assert _roots(http_json("PATCH", roots_url, body={"path": str(deleted)})) == [str(folders.twin)]


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_folder_granted_from_the_card_stays_open_as_itself(
    tmp_path: Path, short_root: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    folders = _make_folders(short_root)
    api_url = get_e2e_api_url()
    probe = setup_file_read_chat(tmp_path, e2e_resource_ledger, "e2egrant", _SYSTEM_PROMPT)

    ui_url = get_e2e_ui_url()
    drive_chat_turn(
        ui_url,
        probe,
        folders.request(folders.spaced / "secret.txt"),
        invoke_skill=False,
        while_open=_tick_the_folder_grant_and_approve(probe, folders.spaced, folders.secret),
    )
    assert _chat_roots(probe.chat_id) == [str(folders.spaced)], "the ticked grant must keep ``shared `` itself open for the chat"

    # A second folder ticked from a card joins the first one; it does not take its place.
    drive_chat_turn(
        ui_url,
        probe,
        folders.request(folders.other / "secret.txt"),
        invoke_skill=False,
        while_open=_tick_the_folder_grant_and_approve(probe, folders.other, folders.other_secret),
    )
    assert _chat_roots(probe.chat_id) == [str(folders.spaced), str(folders.other)], "the second grant must join the first"

    # Another file of the first folder: the chat still holds it, so no card; its secret exists nowhere else.
    def read_without_a_card(client: ChromeMcpClient, page: McpPage) -> None:
        _wait_for_reply_without_card(client, page, probe, folders.second_secret)

    drive_chat_turn(
        ui_url, probe, folders.request(folders.spaced / "second.txt"), invoke_skill=False, while_open=read_without_a_card
    )

    # The folder next to it is another folder: its read still needs the user's say. This turn comes last because the model
    # may try the read again after the refusal, and a card it parks behind would catch the next turn's page.
    def decline_the_twin(client: ChromeMcpClient, page: McpPage) -> None:
        wait_for_approval_card(client, page)
        assert _card(client, page)["folders"] == [str(folders.twin)]
        assert client.evaluate(page, _REJECT_JS, timeout_sec=15.0) is True
        wait_for_state(client, page, f"({{ ready: {_QUEUE_LEN_JS} === 0 }})", timeout_sec=60.0)

    drive_chat_turn(ui_url, probe, folders.request(folders.twin / "secret.txt"), invoke_skill=False, while_open=decline_the_twin)
    assert not any(folders.twin_secret in str(m) for m in stored_messages(probe.chat_id, api_url)), "the declined folder was read"
