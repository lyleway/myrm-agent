"""Router approval mixin: reaction/button approval and decision resume payloads.

[INPUT]
- channels.routing.commands::ApprovalDecision, DenyWithReason (POS: slash command argument parsing and handling.)
- channels.routing.router_host::RouterCommandsHost (POS: typing protocol for mixin host attributes.)
- channels.routing.router_keys::routing_state_key (POS: channel+peer+topic state-map key format.)
- channels.routing.inbound_risk_gate::hold_risky_inbound (POS: risk screening of a sender's text before any agent reads it.)
- channels.routing.pending_approvals::PendingApproval, answer_resume_message (POS: one open approval request of a chat; the message that resumes the asking turn's run with a person's answer.)
- channels.types::InboundMessage, OutboundMessage (POS: channel message types.)
- channels.i18n::get_text (POS: multi-language gateway command replies and system-generated channel messages.)
- services.approvals.registry::ApprovalRegistry (POS: unified approval flow scheduler that persists, pushes and resolves approvals.)

[OUTPUT]
- RouterCommandsApprovalMixin: approval command handlers, reaction/button approval, decision resume payloads

[POS]
Router approval mixin segment. Validates reaction/button approvals and builds harness-compatible resume_value for
LangGraph interrupt resume. A person's answer ends the chat's open request in ``_pending_approvals`` before the run
resumes. Who may answer is decided where the answer is taken, against that open request (``_may_decide``), the same way
for a typed word, a reaction and a button click: in a group only the member who asked. A button click on an approval the
chat holds no request for (one raised outside the chat, an external agent's permission request) is decided by the owner;
one on an approval already settled is not authorised and only marks the card settled. The ends of a request without an
answer (the timeout, a new turn in its session) are in ``router_commands_approval_end``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.channels.i18n import get_text
from app.channels.routing.inbound_risk_gate import hold_risky_inbound
from app.channels.routing.pending_approvals import PendingApproval, answer_resume_message
from app.channels.routing.router_host import RouterCommandsHost
from app.channels.routing.router_keys import routing_state_key
from app.channels.types import InboundMessage, OutboundMessage
from app.services.approvals.external_agent_permission import ACTION_TYPE as EXTERNAL_AGENT_PERMISSION

from ..parsers import ApprovalDecision, DenyWithReason

if TYPE_CHECKING:
    from app.channels.core.bus import MessageBus

logger = logging.getLogger("app.channels.routing.router")

_OUTBOUND_DRAFT = "outbound_draft"


async def _show_outcome_on_card(bus: MessageBus, msg: InboundMessage, chat_id: str, text_key: str, **params: str) -> None:
    """Replace the approval card a button was clicked on with its outcome, so a settled card stops offering buttons."""
    origin_message_id = msg.metadata.get("origin_message_id")
    if origin_message_id and chat_id:
        await bus.edit_channel_message(msg.channel, chat_id, str(origin_message_id), get_text(msg, text_key, **params))


async def _reply_text(bus: MessageBus, msg: InboundMessage, chat_id: str, text_key: str) -> None:
    """Answer ``msg`` in its chat with the catalog message ``text_key``."""
    await bus.publish_outbound(
        OutboundMessage(
            channel=msg.channel,
            recipient_id=chat_id,
            content=get_text(msg, text_key),
            user_id=msg.user_id or "",
            thread_id=msg.thread_id,
            reply_to_id=((msg.message_id or str(msg.metadata.get("message_id", ""))) if msg.is_group else None),
        )
    )


def _may_decide(msg: InboundMessage, asked: PendingApproval) -> bool:
    """Whether the sender of ``msg`` may answer the chat's open request ``asked``.

    The request is the message of the turn that asked, so it says who asked. The chat counts as a group when the request
    or the answer says so: a flag is only evidence when it says group, because a channel that cannot tell what chat an
    event came from reports a direct chat. In a DM whoever writes decides; in a group only the member who asked. A
    missing identity never matches: nobody qualifies when ``msg`` names no sender, or when no member is known to have
    asked.
    """
    if not (asked.message.is_group or msg.is_group):
        return True
    return bool(msg.sender_id) and msg.sender_id == asked.message.sender_id


def _log_refusal(msg: InboundMessage, requester_id: str | None) -> None:
    logger.info(
        "Approval answer refused: sender=%s requester=%s channel=%s chat=%s",
        msg.sender_id,
        requester_id,
        msg.channel,
        msg.chat_id,
    )


class RouterCommandsApprovalMixin:
    """Mixin: reaction/button approval and decision resume payloads."""

    def _has_pending_approval(self: RouterCommandsHost, msg: InboundMessage) -> bool:
        """Check if the session has a pending approval request.

        Used to gate numeric shortcut commands (like plain "1" or "2") so they are only
        read as an approval while the session really waits on one.
        """
        chat_id = msg.chat_id or msg.sender_id
        state_key = routing_state_key(msg.channel, chat_id, msg.thread_id)
        return state_key in self._pending_approvals

    def _is_reaction_approval_valid(self: RouterCommandsHost, msg: InboundMessage) -> bool:
        """Whether an inbound emoji reaction answers the chat's open approval request.

        1. **Pending approval gate** — the chat must wait on an approval
           request. Reactions on stale messages are silently dropped.
        2. **Target match** — when ``target_message_id`` is present in the
           reaction metadata it must equal the stored approval message id;
           otherwise the reaction targets some other message (e.g. an old
           bot reply) and is ignored.

        Who may answer is not decided here: ``_handle_approval_command`` decides it when it takes the request.
        """
        chat_id = msg.chat_id or msg.sender_id
        stored_mid = self._pending_approvals.card_message_id(routing_state_key(msg.channel, chat_id, msg.thread_id))
        if stored_mid is None:
            return False
        target_mid = msg.metadata.get("target_message_id")
        return not (target_mid and stored_mid and str(target_mid) != stored_mid)

    async def _handle_approval_command(
        self: RouterCommandsHost,
        msg: InboundMessage,
        decision: ApprovalDecision | DenyWithReason | list[ApprovalDecision],
    ) -> None:
        """Resume the interrupted agent with the user's three-tier decision.

        Builds a ``resume_value`` payload that mirrors the harness
        ``_batch_decisions`` contract:

        - ``allow_once``   → ``{"type": "approve"}``
        - ``allow_always`` → ``{"type": "approve", "allow_always": True}``
          (harness `add_to_allowlist_if_needed` then persists into the user's
          allowlist; future identical tool invocations bypass ASK gate)
        - ``deny``         → ``{"type": "reject", "feedback": "..."}``
        - ``DenyWithReason`` → ``{"type": "reject", ..., "guidance": "<reason>"}``

        Supports both single and batch decisions; LangGraph resumes the
        interrupted graph state via ``Command(resume=resume_value)``.

        The request is taken only by someone who may decide it (``_may_decide``, against the request itself): the
        answer of anyone else is dropped and the request stays open, with no reply that would show the chat an
        approval is pending. The check runs after the last ``await`` of this handler and directly before the request
        is taken, so the request judged is the request taken.
        """
        chat_id = msg.chat_id or msg.sender_id
        session_key = routing_state_key(msg.channel, chat_id, msg.thread_id)

        if session_key not in self._pending_approvals:
            await _reply_text(self._bus, msg, chat_id, "no_pending_approval")
            return

        if isinstance(decision, DenyWithReason):
            # The reason reaches the agent as guidance, so it passes the gate like any text the sender typed. A held
            # reason leaves the approval pending: the sender is told and can deny again.
            if await hold_risky_inbound(self._bus, msg, decision.reason):
                return
            entry = self._build_decision_entry("deny", msg.channel, reason=decision.reason)
            decisions: list[dict[str, object]] = [entry]
            status_msg = self._status_for_single_decision(msg, "deny", reason=decision.reason)
        elif isinstance(decision, str):
            entry = self._build_decision_entry(decision, msg.channel)
            decisions = [entry]
            status_msg = self._status_for_single_decision(msg, decision)
        else:
            decisions = [self._build_decision_entry(d, msg.channel, batch=True) for d in decision]
            status_msg = self._status_for_batch_decision(msg, decision)

        resume_value: dict[str, object] = {"decisions": decisions}

        asked = self._pending_approvals.get(session_key)
        if asked is not None and not _may_decide(msg, asked):
            _log_refusal(msg, asked.message.sender_id)
            return
        answered = self._pending_approvals.pop(session_key)
        if answered is None:
            # The wait ended while the decision was being checked (the timeout resumed the run, or /stop ended it).
            await _reply_text(self._bus, msg, chat_id, "approval_already_handled")
            return
        if answered.card_message_id:
            await self._bus.edit_channel_message(msg.channel, chat_id, answered.card_message_id, status_msg)

        self._gate.submit(answer_resume_message(msg, resume_value, answered))

    async def _handle_action_button_approval(
        self: RouterCommandsHost,
        msg: InboundMessage,
    ) -> None:
        """Handle an ActionButton callback for approval (``act:approval:{action}:{id}``).

        Closes the loop between ``ApprovalRegistry.create_approval()`` (which
        pushes ActionButtons to IM channels) and the actual resolution. The
        ``msg.content`` arriving here has the format ``approval:{action}:{id}``
        (the ``act:`` transport prefix is stripped by each channel's inbound parser).

        Steps:
          1. Parse action ("approve" / "deny") and approval_id from content.
          2. Authorise the clicker. An approval that is already settled has
             no decision left to authorise: whoever clicks, its card is edited
             to say so and the handler exits. A held channel reply
             (``outbound_draft``) is decided by the owner alone; any other
             approval by the member whose message started the chat's open
             request (``_may_decide``), and, when the chat holds no open
             request, by the owner.
          3. Resolve via ``ApprovalRegistry`` (only PENDING records; one
             settled in the meantime gets the same card edit and the handler
             exits). Resolving a held reply also sends or releases it.
          4. Edit the original IM message to show the outcome and prevent
             further button clicks from appearing actionable.
          5. Convert the decision into a ``resume_value`` and submit to
             ``SessionGate`` so the interrupted LangGraph agent resumes
             (a held reply has no agent run waiting, so it ends at step 4).
        """
        content = msg.content or ""
        parts = content.split(":", 2)
        if len(parts) != 3 or parts[0] != "approval":
            logger.warning("Malformed action button approval content: %r", content)
            return

        action_raw, approval_id = parts[1], parts[2]
        if action_raw not in ("approve", "deny"):
            logger.warning("Unknown action button approval action: %r", action_raw)
            return

        chat_id = msg.chat_id or msg.sender_id
        session_key = routing_state_key(msg.channel, chat_id, msg.thread_id)

        from app.services.approvals.registry import ApprovalRegistry

        # --- Authorisation ---
        pending = await ApprovalRegistry.get_approval(approval_id)
        if pending is not None and pending.status != "PENDING":
            # Nothing is left to decide, so there is nobody to authorise; a stale card is marked settled for any clicker.
            await _show_outcome_on_card(self._bus, msg, chat_id, "approval_already_resolved")
            return
        active = self._active_tasks.get(session_key)
        asked = self._pending_approvals.get(session_key)
        if pending is not None and pending.action_type == _OUTBOUND_DRAFT:
            # No agent task of this chat waits on a held reply, so the request rule below cannot apply, and
            # everyone who can see the card (any group member, any DM sender) would otherwise get to decide.
            if not await self._check_admin_permission(msg):
                logger.info("Held reply decision refused: sender=%s channel=%s", msg.sender_id, msg.channel)
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        recipient_id=chat_id,
                        content=get_text(msg, "approval_requires_admin"),
                        user_id=msg.user_id or "",
                        thread_id=msg.thread_id,
                    )
                )
                return
        elif asked is None:
            # The chat holds no request, so no member is known to have asked this approval (it was raised outside the
            # chat, or by an external agent), and everyone who can see the card would otherwise get to decide: the
            # owner does, as for a held reply.
            if not await self._check_admin_permission(msg):
                _log_refusal(msg, None)
                return
        elif not _may_decide(msg, asked):
            _log_refusal(msg, asked.message.sender_id)
            return

        # --- Resolve in DB ---
        record = await ApprovalRegistry.resolve_approval(
            approval_id=approval_id,
            decision=action_raw,
        )

        if record is None:
            logger.warning("Action button approval: not found or already resolved id=%s", approval_id[:12])
            await _show_outcome_on_card(self._bus, msg, chat_id, "approval_already_resolved")
            return

        logger.info(
            "Action button approval resolved: id=%s action=%s channel=%s sender=%s",
            approval_id[:12],
            action_raw,
            msg.channel,
            msg.sender_id,
        )

        # --- Edit original IM message to show result ---
        outcome_key = "approval_resolved_approved" if action_raw == "approve" else "approval_resolved_denied"
        sender_display = str(msg.metadata.get("username") or msg.sender_id or "")
        await _show_outcome_on_card(self._bus, msg, chat_id, outcome_key, user=sender_display)

        # Neither has an agent run to resume: resolving a held reply has already sent or released it, and resolving an
        # external agent's permission request has woken the delegated turn that asked.
        if record.action_type in (_OUTBOUND_DRAFT, EXTERNAL_AGENT_PERMISSION):
            return

        # --- Resume the interrupted Agent ---
        if active is not None:
            if self._pending_approvals.get(session_key) is not asked:
                # The request the clicker was judged against has ended or been replaced while the decision was
                # stored: it was answered some other way already, and the one open now belongs to its own asker.
                logger.info("Action button approval resumes nothing: its request is no longer open: id=%s", approval_id[:12])
                return
            decision: ApprovalDecision = "allow_once" if action_raw == "approve" else "deny"
            entry = self._build_decision_entry(decision, msg.channel)
            resume_value: dict[str, object] = {"decisions": [entry]}
            answered = self._pending_approvals.pop(session_key)
            self._gate.submit(answer_resume_message(msg, resume_value, answered))
        else:
            # No active task in router (e.g. Agent resumed via WebUI concurrently).
            # DB is already resolved; publish event for any SSE listeners.
            if record.thread_id:
                from app.services.event.app_event_bus import (
                    AppEvent,
                    AppEventType,
                    get_event_bus,
                )

                bus = get_event_bus()
                bus.publish(
                    AppEvent(
                        event_type=AppEventType.APPROVAL_RESOLVED,
                        data={
                            "action": "resume_agent",
                            "approval_id": record.id,
                            "thread_id": record.thread_id,
                            "chat_id": record.chat_id,
                            "agent_id": record.agent_id,
                            "decision": action_raw,
                        },
                    )
                )

    @staticmethod
    def _build_decision_entry(
        decision: ApprovalDecision,
        channel: str,
        *,
        batch: bool = False,
        reason: str | None = None,
    ) -> dict[str, object]:
        """Translate the three-tier decision into the harness decision dict.

        The harness ``apply_approval_decisions`` reads ``allowAlways`` from
        ``decision.extensions`` (camelCase) to drive
        ``add_to_allowlist_if_needed``; mirror that contract exactly.

        When *reason* is provided (from ``/deny <reason>``), it is passed as
        ``guidance`` so the harness injects it as a HumanMessage for the agent
        to course-correct.
        """
        if decision == "allow_always":
            return {"type": "approve", "extensions": {"allowAlways": True}}
        if decision == "allow_once":
            return {"type": "approve"}
        suffix = "batch command" if batch else "command"
        entry: dict[str, object] = {
            "type": "reject",
            "feedback": f"Denied via {channel} channel {suffix}",
        }
        if reason:
            entry["guidance"] = reason
        return entry

    @staticmethod
    def _status_for_single_decision(
        msg: InboundMessage,
        decision: ApprovalDecision,
        *,
        reason: str = "",
    ) -> str:
        if decision == "allow_always":
            return get_text(msg, "approval_always_processing")
        if decision == "allow_once":
            return get_text(msg, "approval_processing")
        if reason:
            return get_text(msg, "approval_denial_with_reason_processing", reason=reason)
        return get_text(msg, "approval_denial_processing")

    @staticmethod
    def _status_for_batch_decision(msg: InboundMessage, decisions: list[ApprovalDecision]) -> str:
        approve_count = sum(1 for d in decisions if d != "deny")
        always_count = sum(1 for d in decisions if d == "allow_always")
        reject_count = len(decisions) - approve_count
        if always_count > 0:
            return get_text(
                msg,
                "approval_batch_processing_always",
                approve_count=approve_count,
                always_count=always_count,
                reject_count=reject_count,
            )
        return get_text(
            msg,
            "approval_batch_processing",
            approve_count=approve_count,
            reject_count=reject_count,
        )
