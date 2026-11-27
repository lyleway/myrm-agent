"""Who may press an approval card's button: the member whose turn asked it, the owner when no turn of the chat did.

Runs ``_handle_action_button_approval`` over a real SQLite in-memory approvals database. A press that is refused leaves
the record pending, so its rightful decider can still press; what a press does once allowed is in ``test_resolve.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.channels.routing.router_keys import routing_session_key
from app.services.approvals.external_agent_permission import ACTION_TYPE as EXTERNAL_AGENT_PERMISSION
from app.services.approvals.external_agent_permission import pending_permission_answers
from tests.channels.routing.action_button.testkit import active_turn, click, make_host, seed, status_of

_CHAT = routing_session_key("telegram", "chat-1")


class TestRequesterRule:
    """A turn of the chat asked: in a group only the member who started it decides, in a DM whoever writes there."""

    @pytest.mark.asyncio
    async def test_group_bystander_is_blocked(self, event_bus: MagicMock):
        host = make_host()
        host._active_tasks[_CHAT] = active_turn("alice")
        await seed("appr-auth-1")

        await host._handle_action_button_approval(click("approval:approve:appr-auth-1", sender_id="bob", is_group=True))

        host._gate.submit.assert_not_called()
        event_bus.publish.assert_not_called()
        assert await status_of("appr-auth-1") == "PENDING"

    @pytest.mark.asyncio
    async def test_group_requester_is_allowed(self):
        host = make_host()
        host._active_tasks[_CHAT] = active_turn("alice")
        await seed("appr-auth-2")

        await host._handle_action_button_approval(click("approval:approve:appr-auth-2", sender_id="alice", is_group=True))

        host._gate.submit.assert_called_once()

    @pytest.mark.asyncio
    async def test_dm_bypasses_bystander_check(self):
        host = make_host()
        host._active_tasks[_CHAT] = active_turn("alice")
        await seed("appr-auth-4")

        await host._handle_action_button_approval(click("approval:approve:appr-auth-4", sender_id="bob", is_group=False))

        host._gate.submit.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_group_click_that_names_no_sender_is_blocked(self):
        host = make_host()
        host._active_tasks[_CHAT] = active_turn("alice")
        await seed("appr-auth-5")

        await host._handle_action_button_approval(click("approval:approve:appr-auth-5", sender_id="", is_group=True))

        host._gate.submit.assert_not_called()
        assert await status_of("appr-auth-5") == "PENDING"

    @pytest.mark.asyncio
    async def test_a_turn_whose_requester_is_unknown_leaves_nobody_to_decide(self):
        host = make_host()
        host._active_tasks[_CHAT] = active_turn("")
        await seed("appr-auth-6")

        await host._handle_action_button_approval(click("approval:approve:appr-auth-6", sender_id="alice", is_group=True))

        host._gate.submit.assert_not_called()
        assert await status_of("appr-auth-6") == "PENDING"


class TestApprovalNoTurnAsked:
    """No turn of the group chat waits on the approval, so there is no requester to compare with: the owner decides."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action_type", ["shell_command", EXTERNAL_AGENT_PERMISSION])
    async def test_anyone_else_is_blocked(self, action_type: str, event_bus: MagicMock):
        host = make_host()
        host._check_admin_permission.return_value = False
        record_id = await seed(f"appr-nobody-{action_type}", action_type=action_type)
        waiting = pending_permission_answers.expect(record_id)

        await host._handle_action_button_approval(click(f"approval:approve:{record_id}", sender_id="bob", is_group=True))

        host._check_admin_permission.assert_awaited_once()
        assert not waiting.done()
        host._gate.submit.assert_not_called()
        event_bus.publish.assert_not_called()
        assert await status_of(record_id) == "PENDING", "the owner can still decide"
        waiting.cancel()
        pending_permission_answers.forget(record_id)

    @pytest.mark.asyncio
    async def test_the_owner_decides(self, event_bus: MagicMock):
        host = make_host()
        await seed("appr-nobody-owner")

        await host._handle_action_button_approval(click("approval:approve:appr-nobody-owner", sender_id="owner", is_group=True))

        host._check_admin_permission.assert_awaited_once()
        assert await status_of("appr-nobody-owner") == "APPROVED"
        resumed = [
            call.args[0].data for call in event_bus.publish.call_args_list if call.args[0].data.get("action") == "resume_agent"
        ]
        assert [data["approval_id"] for data in resumed] == ["appr-nobody-owner"]

    @pytest.mark.asyncio
    async def test_a_dm_needs_no_owner_check(self):
        host = make_host()
        host._check_admin_permission.return_value = False
        await seed("appr-nobody-dm")

        await host._handle_action_button_approval(click("approval:approve:appr-nobody-dm", sender_id="alice", is_group=False))

        host._check_admin_permission.assert_not_awaited()
        assert await status_of("appr-nobody-dm") == "APPROVED"


class TestExternalAgentPermissionClick:
    """An external agent's permission request is answered by the delegated turn waiting on it, not by a resumed run."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("press", "allowed"), [("approve", True), ("deny", False)])
    async def test_a_click_decides_the_request_and_resumes_no_agent_run(self, press: str, allowed: bool):
        host = make_host()
        host._active_tasks[_CHAT] = active_turn("alice")
        await seed("appr-perm-1", action_type=EXTERNAL_AGENT_PERMISSION)
        waiting = pending_permission_answers.expect("appr-perm-1")

        await host._handle_action_button_approval(click(f"approval:{press}:appr-perm-1"))

        assert (await asyncio.wait_for(waiting, timeout=2)).allowed is allowed
        host._gate.submit.assert_not_called()
        assert await status_of("appr-perm-1") == ("APPROVED" if allowed else "REJECTED")

    @pytest.mark.asyncio
    async def test_a_group_bystander_cannot_allow_what_the_agent_asks(self):
        host = make_host()
        host._active_tasks[_CHAT] = active_turn("alice")
        await seed("appr-perm-2", action_type=EXTERNAL_AGENT_PERMISSION)
        waiting = pending_permission_answers.expect("appr-perm-2")

        await host._handle_action_button_approval(click("approval:approve:appr-perm-2", sender_id="bob", is_group=True))

        assert not waiting.done()
        assert await status_of("appr-perm-2") == "PENDING"
        waiting.cancel()
        pending_permission_answers.forget("appr-perm-2")


class TestHeldReplyAuthorisation:
    """A held channel reply (``outbound_draft``) is decided by the owner alone."""

    @pytest.fixture(autouse=True)
    def send_draft(self) -> Iterator[AsyncMock]:
        with patch("app.services.approvals.outbound_draft.send_outbound_draft_payload", new_callable=AsyncMock) as sent:
            yield sent

    @pytest.mark.asyncio
    async def test_the_owner_approves_and_the_reply_is_sent(self, send_draft):
        host = make_host()
        await seed("appr-draft-1", action_type="outbound_draft")

        await host._handle_action_button_approval(click("approval:approve:appr-draft-1", sender_id="owner"))

        send_draft.assert_awaited_once()
        assert await status_of("appr-draft-1") == "APPROVED"
        host._gate.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_owner_denies_and_nothing_is_sent(self, send_draft):
        host = make_host()
        await seed("appr-draft-2", action_type="outbound_draft")

        await host._handle_action_button_approval(click("approval:deny:appr-draft-2", sender_id="owner"))

        send_draft.assert_not_awaited()
        assert await status_of("appr-draft-2") == "REJECTED"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("is_group", [False, True], ids=["dm", "group"])
    async def test_anyone_else_is_refused_and_told_where_to_decide(self, send_draft, is_group):
        host = make_host()
        host._check_admin_permission.return_value = False
        await seed(f"appr-draft-3-{is_group}", action_type="outbound_draft")

        await host._handle_action_button_approval(
            click(f"approval:approve:appr-draft-3-{is_group}", sender_id="bystander", is_group=is_group)
        )

        send_draft.assert_not_awaited()
        assert await status_of(f"appr-draft-3-{is_group}") == "PENDING", "the owner can still decide"
        host._bus.edit_channel_message.assert_not_awaited()
        host._bus.publish_outbound.assert_awaited_once()
        reply = host._bus.publish_outbound.call_args[0][0]
        assert (reply.channel, reply.recipient_id) == ("telegram", "chat-1")
        assert "Only an admin can decide" in reply.content

    @pytest.mark.asyncio
    async def test_running_the_chats_task_grants_nothing_over_a_held_reply(self, send_draft):
        host = make_host()
        host._check_admin_permission.return_value = False
        host._active_tasks[_CHAT] = active_turn("bob")
        await seed("appr-draft-4", action_type="outbound_draft")

        await host._handle_action_button_approval(click("approval:approve:appr-draft-4", sender_id="bob", is_group=True))

        send_draft.assert_not_awaited()
        assert await status_of("appr-draft-4") == "PENDING"

    @pytest.mark.asyncio
    async def test_the_owner_is_not_blocked_by_someone_elses_running_task(self, send_draft):
        host = make_host()
        host._active_tasks[_CHAT] = active_turn("alice")
        await seed("appr-draft-5", action_type="outbound_draft")

        await host._handle_action_button_approval(click("approval:approve:appr-draft-5", sender_id="owner", is_group=True))

        send_draft.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tool_approvals_do_not_ask_for_the_owner_check(self):
        host = make_host()
        host._check_admin_permission.return_value = False
        host._active_tasks[_CHAT] = active_turn("alice")
        await seed("appr-tool-1")

        await host._handle_action_button_approval(click("approval:approve:appr-tool-1"))

        host._gate.submit.assert_called_once()
        host._check_admin_permission.assert_not_awaited()
