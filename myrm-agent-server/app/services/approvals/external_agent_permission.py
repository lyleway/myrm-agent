"""The answers external-agent turns wait for: a person's decision on one permission request, by approval id.

[INPUT]
- myrm_agent_harness.toolkits.acp.types::PermissionVerdict (POS: 外部 Agent 委托协议：权限请求的裁决)
- app.database.models.approval::ApprovalRecord (POS: 审批持久化模型)

[OUTPUT]
- ACTION_TYPE: the approval type of an external agent's permission request
- NO_ANSWER_MESSAGE: what an agent is told when nobody answered its request in time
- DECISION_NOTE_KEY, DECISION_NOTE_CHARS, note_of: the reason a person gave for refusing, kept on the approval and passed to the agent
- verdict_of: the verdict a settled request's status (and note) stands for
- PendingPermissionAnswers: the requests a running turn is waiting on; ``settle`` hands a decided record's verdict to its waiter
- pending_permission_answers: the process-wide instance

[POS]
Join between the approval registry and the turn waiting for it. The registry settles a record here once it is decided
through any door (WebUI, IM button, batch), so a door needs no knowledge of external agents. A turn that ends first
(stopped, timed out) forgets its request itself.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from myrm_agent_harness.toolkits.acp.types import PermissionVerdict

if TYPE_CHECKING:
    from app.database.models.approval import ApprovalRecord

ACTION_TYPE = "external_agent_permission"
NO_ANSWER_MESSAGE = "Nobody answered the permission request in time."
DECISION_NOTE_KEY = "decision_note"
DECISION_NOTE_CHARS = 1_000


class PendingPermissionAnswers:
    """The permission requests running turns wait on, each as a future that a decision resolves."""

    def __init__(self) -> None:
        self._waiting: dict[str, asyncio.Future[PermissionVerdict]] = {}

    @property
    def waiting_count(self) -> int:
        """How many requests turns are waiting on right now."""
        return len(self._waiting)

    def expect(self, approval_id: str) -> asyncio.Future[PermissionVerdict]:
        """The future the decision on ``approval_id`` will resolve; the caller owns it until ``forget``."""
        future: asyncio.Future[PermissionVerdict] = asyncio.get_running_loop().create_future()
        self._waiting[approval_id] = future
        return future

    def forget(self, approval_id: str) -> None:
        """Stop waiting for ``approval_id`` (its turn ended, or it was answered)."""
        self._waiting.pop(approval_id, None)

    def settle(self, record: ApprovalRecord) -> bool:
        """Hand the decision stored on ``record`` to the turn waiting on it; False when no turn waits (any more)."""
        future = self._waiting.pop(record.id, None)
        if future is None:
            return False
        # The decision may come from a request served on another loop than the turn's own.
        future.get_loop().call_soon_threadsafe(_resolve, future, verdict_of(record.status, note_of(record)))
        return True


def note_of(record: ApprovalRecord) -> str | None:
    """The reason the person gave when they decided ``record``, if any."""
    note = (record.payload or {}).get(DECISION_NOTE_KEY)
    return note if isinstance(note, str) and note else None


def verdict_of(status: str, note: str | None = None) -> PermissionVerdict:
    """The verdict a settled request's status stands for: only an approval allows.

    A refusal carries the person's reason, so the agent can do otherwise; an unanswered request says that nobody answered.
    """
    if status == "APPROVED":
        return PermissionVerdict(allowed=True)
    if status == "TIMEOUT":
        return PermissionVerdict(allowed=False, message=NO_ANSWER_MESSAGE)
    return PermissionVerdict(allowed=False, message=note)


def _resolve(future: asyncio.Future[PermissionVerdict], verdict: PermissionVerdict) -> None:
    if not future.done():
        future.set_result(verdict)


pending_permission_answers = PendingPermissionAnswers()
