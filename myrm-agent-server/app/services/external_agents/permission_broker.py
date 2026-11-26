"""The host side of an external agent's permission requests: a person decides, through the approval pipeline.

[INPUT]
- myrm_agent_harness.toolkits.acp.types::PermissionRequest, PermissionVerdict (POS: 外部 Agent 委托协议：权限请求与裁决)
- myrm_agent_harness.api::escape_invisible_unicode, redact_field_text, redact_nested (POS: 展示给人的参数先转义不可见字符再脱敏)
- app.services.approvals.registry::ApprovalRegistry (POS: 统一审批注册与唤醒中枢)
- app.services.approvals.external_agent_permission::ACTION_TYPE, NO_ANSWER_MESSAGE, note_of, pending_permission_answers, verdict_of (POS: 审批决定交还给等待它的回合)

[OUTPUT]
- ChatPermissionBroker: the ``PermissionBroker`` of one chat's delegated turns
- permission_payload: the approval payload of one request, redacted and bounded

[POS]
Each permission request of a delegated turn becomes an ``external_agent_permission`` approval of the chat, shown on every
surface the approval pipeline reaches (WebUI, IM card), and the turn waits for the person's answer. The turn's own time
limit stands still meanwhile (the harness pauses it); how long a person may take is this broker's rule: an unanswered
request is refused after ``DECISION_TTL_SECONDS``. A request whose turn ends first is withdrawn, so no card outlives it.
Everything shown to the person is redacted and bounded: the payload is stored and pushed to every surface.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from myrm_agent_harness.api import escape_invisible_unicode, redact_field_text, redact_nested
from myrm_agent_harness.toolkits.acp.types import PermissionVerdict

from app.services.approvals.external_agent_permission import (
    ACTION_TYPE,
    NO_ANSWER_MESSAGE,
    note_of,
    pending_permission_answers,
    verdict_of,
)
from app.services.approvals.registry import ApprovalRegistry

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.acp.types import PermissionRequest

    from app.services.approvals.external_agent_permission import PendingPermissionAnswers

logger = logging.getLogger(__name__)

DECISION_TTL_SECONDS = 600

# Requests that run commands or remove things are what a batch approval must not wave through unconfirmed.
_HIGH_RISK_KINDS = frozenset({"execute", "delete", "move"})

# Shown values are redacted over a window that holds any secret the shown part could start, then cut to what a card shows.
_SCAN_CHARS = 20_000
_SHOWN_CHARS = 2_000
_SHOWN_PATHS = 20
_SHOWN_PATH_CHARS = 500


def _shown(text: str, limit: int = _SHOWN_CHARS) -> str:
    text = escape_invisible_unicode(text[:_SCAN_CHARS])
    redacted = redact_field_text("", text)
    return redacted if len(redacted) <= limit else f"{redacted[:limit]}…"


def _shown_leaf(key: str, text: str) -> str:
    redacted = redact_field_text(key, escape_invisible_unicode(text[:_SCAN_CHARS]))
    return redacted if len(redacted) <= _SHOWN_CHARS else f"{redacted[:_SHOWN_CHARS]}…"


def permission_payload(request: PermissionRequest, workspace: str) -> dict[str, object]:
    """The approval payload of ``request``: what the person is asked to allow, redacted and bounded."""
    tool_input = redact_nested(dict(request.input), _shown_leaf)
    payload: dict[str, object] = {
        "action_type": ACTION_TYPE,
        "agent": request.agent,
        "tool_name": request.tool_name,
        "kind": request.kind,
        "title": _shown(request.title, 300),
        "tool_input": tool_input if isinstance(tool_input, dict) else {},
        "paths": [_shown(path, _SHOWN_PATH_CHARS) for path in request.paths[:_SHOWN_PATHS]],
        "workspace": workspace,
        "session_id": request.session_id,
    }
    command = request.input.get("command")
    if isinstance(command, str):
        # The batch risk classifier and the card both read the command from the top level of the payload.
        payload["command"] = _shown(command)
    return payload


@dataclass(frozen=True, slots=True)
class ChatPermissionBroker:
    """The ``PermissionBroker`` of one chat: a request becomes an approval, and the turn waits for its decision."""

    agent_id: str
    chat_id: str | None
    workspace: str
    answers: PendingPermissionAnswers = pending_permission_answers
    ttl_seconds: float = DECISION_TTL_SECONDS

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        payload = permission_payload(request, self.workspace)
        record = await ApprovalRegistry.create_approval(
            agent_id=self.agent_id,
            action_type=ACTION_TYPE,
            payload=payload,
            reason=f"{request.agent}: {payload['title']}",
            severity="high" if request.kind in _HIGH_RISK_KINDS else "warning",
            chat_id=self.chat_id,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds),
        )
        answered = self.answers.expect(record.id)
        try:
            # A decision made while the request was still being announced (a slow channel push) found nobody waiting.
            decided = await ApprovalRegistry.get_approval(record.id)
            if decided is not None and decided.status != "PENDING":
                return verdict_of(decided.status, note_of(decided))
            async with asyncio.timeout(self.ttl_seconds):
                return await answered
        except TimeoutError:
            logger.info("external_agent_permission_unanswered approval=%s agent=%s", record.id, request.agent)
            await _withdraw(record.id, status="TIMEOUT", note="Auto-Rejected: no answer in time")
            return PermissionVerdict(allowed=False, message=NO_ANSWER_MESSAGE)
        except asyncio.CancelledError:
            # The turn ended while the person was still deciding: close the card, though this task is being cancelled.
            await asyncio.shield(_withdraw(record.id, status="CANCELLED", note="Withdrawn: the turn ended"))
            raise
        finally:
            self.answers.forget(record.id)


async def _withdraw(approval_id: str, *, status: str, note: str) -> None:
    """Close an approval nobody decided; a failure leaves it to the expiry sweep, which settles it at ``expires_at``."""
    try:
        await ApprovalRegistry.withdraw(approval_id, status=status, note=note)
    except Exception:
        logger.warning("external_agent_permission_withdraw_failed approval=%s", approval_id, exc_info=True)
