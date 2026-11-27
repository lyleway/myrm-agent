"""JSON results the image and video tools share: errors, started tasks and task status.

[INPUT]
- app.lifecycle.task_worker::get_task_store (POS: task queue singleton; imported at call time)
- app.tasks.serializer::serialize_media_task (POS: media task status serializer)
- app.ai_agents.media_tools.workspace_export::WorkspaceExporter (POS: copies a result's media into the workspace)

[OUTPUT]
- tool_error(): ``{"error": ...}`` result
- media_task_store(): the task queue store, raising ``RuntimeError`` before the worker has started
- started_tasks_result(): ``{"tasks": [...]}`` answer of a generate call, with what to do while the tasks run
- MAX_WAIT_SECONDS / WAIT_SECONDS_DESCRIPTION: bound and wording of the ``wait_seconds`` parameter of ``status``
- media_task_status_result(): result of ``action='status'``, optionally waiting for the task and completing a finished
  result with its workspace copies, with explicit errors

[POS]
The generate and status halves of ``image_tool`` and ``video_tool`` answer identically, so they are built once here:
a call either returns the tasks it started or an explicit error, never a result that hides a failure.
"""

from __future__ import annotations

import json
import logging
from asyncio import sleep
from collections.abc import Sequence
from time import monotonic

from myrm_agent_harness.toolkits.tasks import SQLiteTaskStore, Task

from app.ai_agents.media_tools.workspace_export import WorkspaceExporter
from app.tasks.serializer import serialize_media_task

logger = logging.getLogger(__name__)

# Well under the 300 s the tool executor allows image_tool and video_tool.
MAX_WAIT_SECONDS = 90
WAIT_SECONDS_DESCRIPTION = (
    f"Only for action='status': wait up to this many seconds (1-{MAX_WAIT_SECONDS}) for the task to finish before "
    "answering. Leave it out to get the current state at once; use it only when your next step needs the result."
)

_POLL_INTERVAL_SECONDS = 1.0

# Without it a model that has just started a task checks on it again and again: every check answers "still running".
_AFTER_START_GUIDANCE = (
    "Do not keep checking on it: tell the user it is in progress; call status with wait_seconds only if your next "
    "step needs the result or the user asked to be told when it is done."
)


def tool_error(message: str, **fields: str) -> str:
    return json.dumps({"error": message, **fields}, ensure_ascii=False)


def media_task_store() -> SQLiteTaskStore:
    """The task queue store.

    Imported at call time: the task worker module imports the executors, and the executors import this package.

    Raises:
        RuntimeError: The task worker has not started, so there is no store yet.
    """
    from app.lifecycle.task_worker import get_task_store

    return get_task_store()


def started_tasks_result(task_type: str, task_ids: Sequence[str], *, message: str) -> str:
    """Answer of a generate call: one entry per task that was queued."""
    tasks = [{"task_id": task_id, "task_type": task_type, "status": "pending"} for task_id in task_ids]
    return json.dumps({"tasks": tasks, "message": f"{message} {_AFTER_START_GUIDANCE}"}, ensure_ascii=False)


async def _wait_until_finished(store: SQLiteTaskStore, task: Task, wait_seconds: int) -> Task | None:
    """The task once it has finished, or its last state when ``wait_seconds`` run out; ``None`` if it was deleted."""
    deadline = monotonic() + wait_seconds
    while not task.is_terminal():
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        await sleep(min(_POLL_INTERVAL_SECONDS, remaining))
        refreshed = await store.get_task(task.task_id)
        if refreshed is None:
            return None
        task = refreshed
    return task


async def media_task_status_result(
    task_id: str | None,
    wait_seconds: int | None = None,
    *,
    exporter: WorkspaceExporter | None = None,
) -> str:
    """State and result of one task, or the explicit reason there is none to report.

    With ``wait_seconds`` the answer comes as soon as the task has finished, at the latest after that many seconds.
    With ``exporter`` the result of a finished task of its chat also names the copies of the media in the workspace.
    """
    if not task_id:
        return tool_error("task_id is required when action=status")
    try:
        store = media_task_store()
        task = await store.get_task(task_id)
        if task is not None and wait_seconds:
            task = await _wait_until_finished(store, task, wait_seconds)
    except RuntimeError as exc:
        logger.warning("Task store unavailable for media status lookup: %s", exc)
        return tool_error("Task store unavailable", task_id=task_id)
    if task is None:
        return tool_error("Task not found", task_id=task_id)
    status = serialize_media_task(task)
    if exporter is not None:
        status = await exporter.complete_status(task, status)
    return json.dumps(status, ensure_ascii=False)
