"""Progress SSE helper — one ``plan_update`` snapshot per todo_write.

[INPUT]
- progress.schemas::TodoStore (POS: todo models)
- core.events.types::AgentEventType (POS: names of the events the stream dispatcher forwards)
- utils.event_utils::emit_custom_event_sync (POS: custom-stream emitter for synchronous tools)

[OUTPUT]
- emit_plan_update: put the whole plan, ``{goal, revision, todos}``, on the run's custom stream

[POS]
Tells the UI that the plan file changed. One event per todo_write, after the file is written, carrying the revision
the file now holds.
"""

from __future__ import annotations

import logging

from myrm_agent_harness.agent.meta_tools.progress.schemas import TodoStore
from myrm_agent_harness.core.events.types import AgentEventType
from myrm_agent_harness.utils.event_utils import emit_custom_event_sync

logger = logging.getLogger(__name__)


def emit_plan_update(store: TodoStore) -> None:
    """Emit the plan; a failure to emit is logged and never fails the todo_write that already wrote the file."""
    snapshot: dict[str, object] = {
        "goal": store.goal,
        "revision": store.revision,
        "todos": [item.model_dump() for item in store.todos],
    }
    try:
        emit_custom_event_sync(AgentEventType.PLAN_UPDATE.value, snapshot)
    except Exception as exc:
        logger.warning("Failed to emit plan_update: %s", exc)
