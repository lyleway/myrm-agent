"""Chrome LIVE E2E: a user types a raw ``/learn`` message in the composer and the live model saves the skill.

Nothing is seeded. The composer sends the typed text as it is; the server turns a message that starts with ``/learn``
into the skill-authoring prompt; the model calls ``skill_manage_tool``; the skill lands in the catalog with the
SKILL.md the user described; the stream hands the page the step's batch id, so the chat offers the undo hint for the
save; the assistant's closing reply is persisted. The test then undoes the batch, so the catalog is left as found.

Formal run::

    MYRM_E2E_LANE=LIVE_AGENT ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_learn_command_live_chrome_e2e.py
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cdp_chat.support import (  # noqa: E402
    dump_backend_log_on_failure,
    snapshot_backend_log_offset,
    wait_e2e_provider_ready,
)

from tests.e2e.test_skill_manage_live_agent_chrome_e2e import (  # noqa: E402
    _AGENT_TOOLS_APPLIED_JS,
    _SKILL_DESCRIPTION,
    _create_agent,
    _create_chat,
    _describe_tool_steps,
    _LiveTurn,
    _saved_skill_id,
    _skill_manage_batch_ids,
    _wait_for_undo_hint,
)
from tests.e2e.test_skill_manage_undo_rollback_chrome_e2e import (  # noqa: E402
    _fetch_skill_md,
    wait_until_api_is_quiet,
)
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
from tests.support.chrome_skill_hooks_observe import completed_assistant_replies, stored_messages  # noqa: E402
from tests.support.chrome_skill_undo_probes import INSTALL_UNDO_TRACE_JS  # noqa: E402
from tests.support.e2e_provider_seed import seed_live_e2e_providers  # noqa: E402
from tests.support.e2e_runtime_guard import E2EResourceLedger  # noqa: E402


def _learn_request(name: str, marker: str) -> str:
    """One line, as a user types it: the skill to author, its name, description and one body line."""
    return (
        f"/learn Author one skill: name={name}, description={_SKILL_DESCRIPTION}. "
        f"The SKILL.md body is a heading and one line that is exactly: {marker} ."
    )


def _roll_back(api_url: str, batch_id: str) -> None:
    """Undo one committed batch through the endpoint the chat's undo hint calls."""
    answer = http_json("POST", f"{api_url}/api/v1/skill-optimization/mutations/{batch_id}/rollback", timeout_sec=60.0)
    assert isinstance(answer, dict), answer
    receipt = answer.get("data")
    assert (receipt if isinstance(receipt, dict) else answer).get("status"), f"rollback of {batch_id} answered: {answer}"


def _wait_for_completed_reply(chat_id: str, api_url: str, *, timeout_sec: float) -> str:
    """The newest assistant reply the server marked complete; the words are the model's, so nothing is matched."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        replies = completed_assistant_replies(stored_messages(chat_id, api_url))
        if replies and replies[-1].strip():
            return replies[-1]
        time.sleep(2.0)
    pytest.fail(f"no completed assistant reply for chat {chat_id} within {timeout_sec:.0f}s")


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_learn_typed_in_the_composer_saves_a_skill_through_the_live_model(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    started_at = time.time()
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    seed_live_e2e_providers(api_url)
    if not wait_e2e_provider_ready(timeout_sec=90.0):
        pytest.fail("Provider not ready — run ./myrm-dev ready --chrome")

    suffix = uuid.uuid4().hex[:8]
    skill_name = f"e2e-learn-{suffix}"
    marker = f"E2E_LEARN_MARKER_{suffix}"
    agent_id = _create_agent(api_url, suffix)
    e2e_resource_ledger.register("agent", agent_id)
    chat_id = f"e2elearn{suffix}"
    _create_chat(api_url, chat_id, agent_id)
    e2e_resource_ledger.register("chat", chat_id)
    probe = SkillChat(
        skill_name=skill_name,
        agent_id=agent_id,
        chat_id=chat_id,
        out=tmp_path,
        log_offset=snapshot_backend_log_offset(api_url),
    )
    chat_url = f"{ui_url.rstrip('/')}{probe.ui_path}"
    turn = _LiveTurn(chat_id=chat_id, chat_url=chat_url, api_url=api_url, started_at=started_at)
    saved_batches: list[str] = []

    def agent_tools_applied(client: ChromeMcpClient, page: McpPage) -> None:
        probe_js = f"({_AGENT_TOOLS_APPLIED_JS})({json.dumps(agent_id)}, {json.dumps('skill_manage')})"
        applied = wait_for_state(client, page, probe_js, timeout_sec=90.0, page_url=chat_url)
        assert applied.get("ready") is True, applied
        wait_until_api_is_quiet(client, page, api_url, stage="before-send")
        traced = client.evaluate(page, INSTALL_UNDO_TRACE_JS, timeout_sec=15.0)
        assert isinstance(traced, dict) and traced.get("ok") is True, traced

    def after_send(client: ChromeMcpClient, page: McpPage) -> None:
        reply = _wait_for_completed_reply(chat_id, api_url, timeout_sec=240.0)
        stored = stored_messages(chat_id, api_url)
        steps = _describe_tool_steps(stored)
        batch_ids = _skill_manage_batch_ids(stored)
        saved_batches.extend(batch_ids)
        assert batch_ids, (
            f"no skill_manage_tool step with a batch id was persisted; the agent did:\n{steps}\n{describe_page(client, page)}"
        )
        skill_id = _saved_skill_id(api_url, skill_name)
        assert skill_id, f"/learn did not save {skill_name!r}; the agent did:\n{steps}\nreply: {reply[:400]}"
        saved_md = _fetch_skill_md(api_url, quote(skill_id, safe=""))
        assert marker in saved_md, f"the saved SKILL.md lost the marker the user asked for:\n{saved_md[:600]}"
        print(f"E2E_LEARN_SAVED: id={skill_id} batches={json.dumps(batch_ids)} reply={reply[:200]!r}", flush=True)
        _wait_for_undo_hint(client, page, turn)

    # The private backend and its log are reaped at teardown, so a failed turn has to print its own server evidence.
    with dump_backend_log_on_failure(api_url):
        wire = drive_chat_turn(
            ui_url,
            probe,
            _learn_request(skill_name, marker),
            invoke_skill=False,
            before_send=agent_tools_applied,
            while_open=after_send,
        )
    assert wire.startswith("/learn"), f"the composer must hand the server the raw /learn message, it sent: {wire[:200]!r}"

    for batch_id in saved_batches:
        _roll_back(api_url, batch_id)
    deadline = time.monotonic() + 60.0
    while _saved_skill_id(api_url, skill_name) is not None:
        assert time.monotonic() < deadline, f"skill {skill_name!r} survived the rollback of its batch"
        time.sleep(2.0)
