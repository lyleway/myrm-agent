"""What an approval card's button does once the clicker may press it: parse, resolve in the database, edit the card, resume.

Runs ``_handle_action_button_approval`` over a real SQLite in-memory approvals database; only the event bus is a mock.
Who may press the button is in ``test_authority.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.channels.routing.router_keys import routing_session_key
from tests.channels.routing.action_button.testkit import active_turn, click, make_host, seed, status_of


class TestActionButtonParsing:
    @pytest.mark.asyncio
    async def test_malformed_content_is_rejected(self):
        host = make_host()
        await host._handle_action_button_approval(click("bad-content"))
        host._gate.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_parts_is_rejected(self):
        host = make_host()
        await host._handle_action_button_approval(click("approval:approve"))
        host._gate.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_wrong_prefix_is_rejected(self):
        host = make_host()
        await host._handle_action_button_approval(click("reaction:approve:id-123"))
        host._gate.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_action_is_rejected(self):
        host = make_host()
        await host._handle_action_button_approval(click("approval:cancel:id-123"))
        host._gate.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_content_is_rejected(self):
        host = make_host()
        await host._handle_action_button_approval(click(""))
        host._gate.submit.assert_not_called()


class TestActionButtonResolve:
    @pytest.mark.asyncio
    async def test_approve_resumes_agent(self):
        host = make_host()
        key = routing_session_key("telegram", "chat-1")
        host._active_tasks[key] = active_turn("alice")
        await seed("appr-resolve-1")
        await host._handle_action_button_approval(click("approval:approve:appr-resolve-1"))

        host._gate.submit.assert_called_once()
        resume = host._gate.submit.call_args[0][0].resume_value
        assert resume["decisions"][0]["type"] == "approve"

    @pytest.mark.asyncio
    async def test_deny_resumes_agent(self):
        host = make_host()
        key = routing_session_key("telegram", "chat-1")
        host._active_tasks[key] = active_turn("alice")
        await seed("appr-resolve-2")
        await host._handle_action_button_approval(click("approval:deny:appr-resolve-2"))

        host._gate.submit.assert_called_once()
        resume = host._gate.submit.call_args[0][0].resume_value
        assert resume["decisions"][0]["type"] == "reject"

    @pytest.mark.asyncio
    async def test_duplicate_click_does_not_resume(self):
        host = make_host()
        key = routing_session_key("telegram", "chat-1")
        host._active_tasks[key] = active_turn("alice")
        await seed("appr-resolve-3")

        await host._handle_action_button_approval(click("approval:approve:appr-resolve-3"))
        host._gate.submit.assert_called_once()

        host._gate.submit.reset_mock()
        await host._handle_action_button_approval(click("approval:deny:appr-resolve-3"))
        host._gate.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_nonexistent_id_does_not_resume(self):
        host = make_host()
        await host._handle_action_button_approval(click("approval:approve:nonexistent-id-xyz"))
        host._gate.submit.assert_not_called()


class TestActionButtonMessageEditing:
    @pytest.mark.asyncio
    async def test_edits_original_message_on_approve(self):
        host = make_host()
        key = routing_session_key("telegram", "chat-1")
        host._active_tasks[key] = active_turn("alice")
        await seed("appr-edit-1")
        await host._handle_action_button_approval(click("approval:approve:appr-edit-1", origin_message_id="orig-999"))

        host._bus.edit_channel_message.assert_awaited_once()
        call_args = host._bus.edit_channel_message.call_args
        assert call_args[0][2] == "orig-999"
        assert "Approved" in call_args[0][3]

    @pytest.mark.asyncio
    async def test_no_edit_when_origin_missing(self):
        host = make_host()
        key = routing_session_key("telegram", "chat-1")
        host._active_tasks[key] = active_turn("alice")
        await seed("appr-edit-2")
        await host._handle_action_button_approval(click("approval:approve:appr-edit-2", origin_message_id=None))
        host._bus.edit_channel_message.assert_not_awaited()
        host._gate.submit.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("action", "locale", "expected"),
        [
            ("approve", None, "✅ Approved by alice"),
            ("deny", None, "🚫 Denied by alice"),
            ("approve", "zh-CN", "✅ 已由 alice 批准"),
            ("deny", "zh-CN", "🚫 已由 alice 拒绝"),
        ],
    )
    async def test_status_line_reads_naturally_in_the_clickers_language(self, action, locale, expected):
        host = make_host()
        host._active_tasks[routing_session_key("telegram", "chat-1")] = active_turn("alice")
        record_id = await seed(f"appr-status-{action}-{locale}")
        await host._handle_action_button_approval(click(f"approval:{action}:{record_id}", locale=locale))

        assert host._bus.edit_channel_message.call_args[0][3] == expected


class TestStaleClick:
    """A card whose request is already settled says so instead of staying clickable."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("locale", "expected"),
        [(None, "This request has already been handled."), ("zh-CN", "这个请求已经处理过了。")],
    )
    async def test_the_card_is_edited_to_say_it_was_handled(self, locale, expected):
        host = make_host()
        record_id = await seed(f"appr-stale-{locale}", status="APPROVED")

        await host._handle_action_button_approval(
            click(f"approval:approve:{record_id}", origin_message_id="orig-5", locale=locale)
        )

        host._gate.submit.assert_not_called()
        host._bus.edit_channel_message.assert_awaited_once_with("telegram", "chat-1", "orig-5", expected)

    @pytest.mark.asyncio
    async def test_nothing_is_edited_when_the_click_carries_no_card(self):
        host = make_host()
        record_id = await seed("appr-stale-nocard", status="REJECTED")

        await host._handle_action_button_approval(click(f"approval:approve:{record_id}", origin_message_id=None))

        host._bus.edit_channel_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_late_click_cannot_send_an_expired_held_reply(self):
        host = make_host()
        record_id = await seed("appr-stale-draft", status="TIMEOUT", action_type="outbound_draft")

        with patch("app.services.approvals.outbound_draft.send_outbound_draft_payload", new_callable=AsyncMock) as sent:
            await host._handle_action_button_approval(click(f"approval:approve:{record_id}", sender_id="owner"))

        sent.assert_not_awaited()
        host._bus.edit_channel_message.assert_awaited_once()
        assert "already been handled" in host._bus.edit_channel_message.call_args[0][3]


class TestActionButtonSSEFallback:
    @pytest.mark.asyncio
    async def test_no_active_task_publishes_sse_event(self, event_bus: MagicMock):
        host = make_host()
        await seed("appr-sse-1")

        await host._handle_action_button_approval(click("approval:approve:appr-sse-1"))

        host._gate.submit.assert_not_called()
        [event] = [e for e in (c.args[0] for c in event_bus.publish.call_args_list) if e.data.get("action") == "resume_agent"]
        assert event.data["approval_id"] == "appr-sse-1"
        assert event.data["decision"] == "approve"


class TestNotificationCardContract:
    @pytest.mark.asyncio
    async def test_every_button_of_the_owner_card_is_understood_by_the_handler(self):
        """The card the owner receives and the handler that reads its clicks must agree on the action ids."""
        from app.core.notifications.approval_card import build_approval_card

        decided = set()
        for index in range(2):
            record_id = f"appr-card-{index}"
            await seed(record_id, action_type="outbound_draft")
            _, components = build_approval_card({"approval_id": record_id, "action_type": "outbound_draft"}, "telegram", "en")

            with patch("app.services.approvals.outbound_draft.send_outbound_draft_payload", new_callable=AsyncMock):
                await make_host()._handle_action_button_approval(click(components[0][index].action_id))
            decided.add(await status_of(record_id))

        assert decided == {"APPROVED", "REJECTED"}
