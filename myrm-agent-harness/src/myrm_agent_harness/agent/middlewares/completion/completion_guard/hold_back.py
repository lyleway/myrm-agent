"""How a held-back answer stays in the conversation, and what the model is owed on its next request.

When the completion guard holds an answer back it routes the run to the model (``jump_to="model"``) and leaves the
instruction pending in the turn's guard state; ``directive_middleware`` places it behind the message it follows on
the next request and on every later request of the run. This module decides which message the instruction follows:
the draft itself.

[INPUT]
- agent.middlewares.completion.completion_guard.directive::BlockReasons, render_completion_directive (POS: what the model reads for each held-back answer)
- agent.middlewares.completion.completion_guard.events::log_guard_decision, announce_superseded_draft (POS: records the decision and tells the stream the draft is superseded)
- agent.middlewares.completion.completion_guard.state::PendingDirective, RunGuardState (POS: the instruction pending for the next model request)
- agent.security.guards.loop_guard::CallRecord (POS: one recorded tool call)

[OUTPUT]
- hold_back(): the state update that routes the run back to the model, or None when the draft cannot be addressed

[POS]
The delivery half of a hold-back. Nothing the guard says to the model is stored in the conversation, shown as a task
step, approved, counted by the loop guard or scanned as tool output.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from langchain_core.messages import AIMessage

from myrm_agent_harness.agent.middlewares.completion.completion_guard.directive import (
    BlockReasons,
    render_completion_directive,
)
from myrm_agent_harness.agent.middlewares.completion.completion_guard.events import (
    announce_superseded_draft,
    log_guard_decision,
)
from myrm_agent_harness.agent.middlewares.completion.completion_guard.state import PendingDirective, RunGuardState
from myrm_agent_harness.agent.security.guards.loop_guard import CallRecord

logger = logging.getLogger(__name__)


async def hold_back(
    run_state: RunGuardState,
    draft: AIMessage,
    reasons: BlockReasons,
    records: Sequence[CallRecord],
    workspace_root: str | None,
    *,
    rejection: int,
    max_rejections: int,
) -> dict[str, object] | None:
    """Route the run back to the model, with the instruction owed to it pending for the next request.

    The draft stays in the conversation and the instruction follows it. A message the runtime stamped no id on
    cannot be addressed, so that answer is let through.
    """
    if draft.id is None:
        logger.warning(
            "[CompletionGuard] Held-back answer cannot be addressed (message without id); letting it through."
        )
        return None
    await log_guard_decision(reasons, rejection=rejection, max_rejections=max_rejections)
    await announce_superseded_draft()
    run_state.pending_directive = PendingDirective(
        anchor_id=draft.id,
        text=render_completion_directive(reasons, records=records, workspace_root=workspace_root),
    )
    return {"jump_to": "model"}


__all__ = ["hold_back"]
