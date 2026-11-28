"""The manual compaction run: what the page asks about while a compaction it requested goes on in the background.

[INPUT]
- app.database.connection::get_session (POS: the run's own database session; the request that started the run is gone)
- app.services.chat.compact_service::compact_chat (POS: the compaction itself, imported when a run begins)
- app.core.utils.errors::validation_error, internal_error (POS: the failure classes a failed run is reported with)
- app.services.chat.compact._constants::COMPACT_RUN_DRAIN_TIMEOUT_S, COMPACT_RUN_HISTORY (POS: shutdown wait, runs kept)

[OUTPUT]
- CompactRunState / CompactRunFailure / CompactRun: lifecycle states, why a run failed, and the record of a run
- start_compact_run: start the run for a chat in the background, or join the one in flight
- get_compact_run: a run of a chat by its id
- drain_compact_runs: wait for the runs in flight (shutdown)

[POS]
A compaction has a model summarise a whole history and takes tens of seconds to minutes, longer than a proxy between the
page and this server lets one request wait (Next's rewrite 30 s, a customer's nginx or load balancer 60 s). The request
therefore only starts the run and names it, and the page asks for that run by its id until it ends.
Runs are kept by id, the newest ``COMPACT_RUN_HISTORY`` of them: a page reads the run it started even after another
request for the same chat started a later one, and what is kept does not grow with the compactions callers start. A
request for a chat whose run is still going joins that run, because a second compaction of one chat would only wait on
the first one's lock. A run still going is never dropped. The registry lives in memory, like the memory reindex and wiki
maintenance runs: this server is one process, and a run the registry no longer knows (after a restart) is one the page
reads as failed. The run holds its task, the event loop keeps only a weak reference to one, and gives it up when it
ends. The per-chat compaction lock stays in the service, so the idle and pre-reply compactions that take it answer a run
with ``concurrent_compaction_in_progress`` exactly as they answered a request.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial

from app.core.utils.errors import StandardHTTPException, internal_error, validation_error
from app.database.connection import get_session
from app.schemas.responses import StandardErrorResponse
from app.services.chat.compact._constants import COMPACT_RUN_DRAIN_TIMEOUT_S, COMPACT_RUN_HISTORY
from app.services.chat.compact._types import CompactResult

logger = logging.getLogger(__name__)

_INTERRUPTED = "Compaction was interrupted before it finished"


class CompactRunState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CompactRunFailure:
    """Why a run failed, as the request would have been answered while it still waited: status, business code, message."""

    status_code: int
    code: int
    message: str


@dataclass(slots=True)
class CompactRun:
    chat_id: str
    # The topic of the request that started the run; a request that joins the run does not change it.
    focus_topic: str
    # What the page asks for: a later run of the same chat does not take this one's answer away.
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: CompactRunState = CompactRunState.RUNNING
    # A compaction the service declined is a finished run with a result: ``compacted`` is False and ``reason`` says why.
    result: CompactResult | None = None
    failure: CompactRunFailure | None = None
    task: asyncio.Task[None] | None = None


# By run id, oldest first.
_runs: dict[str, CompactRun] = {}


def start_compact_run(chat_id: str, focus_topic: str) -> CompactRun:
    """Start compacting the chat in the background, or hand back the run still going on for it.

    Nothing is awaited between the check and the claim, so two requests cannot both start a run.
    """
    for going in _runs.values():
        if going.chat_id == chat_id and going.state is CompactRunState.RUNNING:
            return going
    run = CompactRun(chat_id=chat_id, focus_topic=focus_topic)
    _runs[run.run_id] = run
    _drop_surplus_ended_runs()
    run.task = asyncio.create_task(_execute(run), name=f"compact-run:{chat_id}")
    run.task.add_done_callback(partial(_settle, run))
    return run


def get_compact_run(chat_id: str, run_id: str) -> CompactRun | None:
    """The run, if the registry still holds it and it is one of this chat's: the path names both, so both must match."""
    run = _runs.get(run_id)
    return run if run is not None and run.chat_id == chat_id else None


def _drop_surplus_ended_runs() -> None:
    """Keep the registry within ``COMPACT_RUN_HISTORY`` by dropping the oldest ended runs; a run still going stays."""
    surplus = len(_runs) - COMPACT_RUN_HISTORY
    if surplus > 0:
        ended = [run_id for run_id, run in _runs.items() if run.state is not CompactRunState.RUNNING]
        for run_id in ended[:surplus]:
            del _runs[run_id]


async def drain_compact_runs(timeout_s: float = COMPACT_RUN_DRAIN_TIMEOUT_S) -> None:
    """Wait, up to ``timeout_s``, for the runs in flight to end; one still going after that is left to the shutdown."""
    tasks = [run.task for run in _runs.values() if run.task is not None]
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=timeout_s)
    if pending:
        logger.warning("%d manual compaction run(s) still running after %gs; the shutdown cuts them off", len(pending), timeout_s)


async def _execute(run: CompactRun) -> None:
    try:
        from app.services.chat.compact_service import compact_chat

        async with get_session() as db:
            result = await compact_chat(db, run.chat_id, focus_topic=run.focus_topic, explicit_request=True)
    except ValueError as exc:
        _fail(run, validation_error(str(exc)))
    except Exception as exc:
        _fail(run, internal_error(operation="Compact chat context", exception=exc))
    else:
        run.result = result
        run.state = CompactRunState.SUCCEEDED


def _fail(run: CompactRun, error: StandardHTTPException) -> None:
    # The factories put the whole error envelope in ``detail``: the code and the redacted message are read from it.
    envelope = StandardErrorResponse.model_validate(error.detail)
    run.failure = CompactRunFailure(status_code=error.status_code, code=envelope.code, message=envelope.message)
    run.state = CompactRunState.FAILED


def _settle(run: CompactRun, task: asyncio.Task[None]) -> None:
    """Runs once the task is over, however it ended: a run is never left running with no task behind it.

    A task cancelled before its first step never enters ``_execute``, so only here can that run be told to have failed.
    """
    run.task = None
    if not task.cancelled() and (crash := task.exception()) is not None:
        logger.error("Manual compaction run for chat %s crashed", run.chat_id, exc_info=crash)
    if run.state is CompactRunState.RUNNING:
        _fail(run, internal_error(message=_INTERRUPTED))
