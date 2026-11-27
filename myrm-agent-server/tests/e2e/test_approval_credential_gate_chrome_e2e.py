"""Chrome LIVE E2E: the one-time credential form of a bash approval card refuses a variable that configures git.

Real business flow, no mocks and no API shortcut for the staging request:

    user sends a message -> live model calls the bash tool -> the run parks behind the approval card
    -> user opens the card's "password or key for this command" section and types GIT_CONFIG_COUNT with a value
    -> the server refuses the name: the card says to check it, and holds nothing
    -> user types an ordinary name -> the server holds it and the section counts it
    -> user presses Approve -> the command runs and the model answers.

A variable named ``GIT_CONFIG_*`` hands every ``git`` the command starts a program to run (``core.sshCommand``,
``alias.x=!cmd``). The server is the one place that decides which names a command may be handed, so what the user
sees must be its refusal, not a staged item.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_approval_credential_gate_chrome_e2e.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.chrome_approval_credentials import (
    STAGE_REFUSED,
    STAGED_READY,
    open_credential_form,
    stage_credential,
)
from tests.support.chrome_approval_turn import SHORT_ANSWER, bash_request, run_one_turn
from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, wait_for_state
from tests.support.chrome_page_text import page_text_matches_js
from tests.support.chrome_skill_hooks_approval import approve_cards_until_reply, wait_for_approval_card
from tests.support.chrome_skill_hooks_live_e2e import DONE_TOKEN, SkillChat
from tests.support.e2e_runtime_guard import E2EResourceLedger

_REFUSED_NAME = "GIT_CONFIG_COUNT"
_ORDINARY_NAME = "E2E_DB_PASS"


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_the_credential_form_refuses_a_git_configuration_name_and_stages_an_ordinary_one(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    def fill_the_form_then_approve(client: ChromeMcpClient, page: McpPage, probe: SkillChat, chat_url: str) -> None:
        wait_for_approval_card(client, page)
        open_credential_form(client, page)

        stage_credential(client, page, _REFUSED_NAME, "1")
        wait_for_state(client, page, page_text_matches_js(STAGE_REFUSED), timeout_sec=30.0)
        counted = client.evaluate(page, page_text_matches_js(STAGED_READY), timeout_sec=15.0)
        assert isinstance(counted, dict) and counted.get("ready") is False, f"{_REFUSED_NAME} was staged: {counted}"

        # The refusal leaves the form as it was: the ordinary name replaces the refused one and goes through.
        stage_credential(client, page, _ORDINARY_NAME, "synthetic-secret-value")
        wait_for_state(client, page, page_text_matches_js(STAGED_READY), timeout_sec=30.0)

        approve_cards_until_reply(client, page, probe, DONE_TOKEN, timeout_sec=240.0, tolerate_closing_note=True)

    run_one_turn(tmp_path, e2e_resource_ledger, "credgate", bash_request(SHORT_ANSWER), fill_the_form_then_approve)
