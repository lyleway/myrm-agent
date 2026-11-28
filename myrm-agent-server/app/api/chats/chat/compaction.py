from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.errors import internal_error, not_found_error, validation_error
from app.core.utils.response_utils import success_response
from app.database.connection import get_db
from app.database.dto import (
    UpdateSummaryRequest,
)
from app.schemas.responses import StandardSuccessResponse
from app.services.chat.chat_service import ChatService
from app.services.chat.compact.run import CompactRun, get_compact_run, start_compact_run

router = APIRouter()


class CompactRequest(BaseModel):
    focus_topic: str = Field(default="", max_length=200)


def _run_data(run: CompactRun) -> dict[str, object]:
    """What the page reads of a run: its id and state, and the result or the failure once the run has ended."""
    data: dict[str, object] = {"run_id": run.run_id, "status": run.state.value}
    if run.result is not None:
        data["result"] = {
            "compacted": run.result.compacted,
            "original_tokens": run.result.original_tokens,
            "summary_tokens": run.result.summary_tokens,
            "tokens_saved": run.result.tokens_saved,
            "message_count": run.result.message_count,
            "backup_path": run.result.backup_path,
            "reason": run.result.reason,
            "focus_topic": run.focus_topic,
        }
    if run.failure is not None:
        data["error"] = {"http_status": run.failure.status_code, "code": run.failure.code, "message": run.failure.message}
    return data


@router.post("/{chat_id}/compact", response_model=StandardSuccessResponse, status_code=202)
async def start_compaction(chat_id: str, body: CompactRequest | None = None) -> JSONResponse:
    """Start compacting the chat context in the background and answer at once, naming the run that does it.

    The page reads the outcome from ``/compact/runs/{run_id}``: the compaction has a model summarise the history and
    outlasts what a proxy lets one request wait. Original messages are preserved: the summary is used by the Agent layer
    to reduce token cost on subsequent interactions. The caller asked for this compaction, so the anti-thrash guard that
    holds back automatic attempts does not apply. A chat whose run is still going is not compacted twice: the request
    joins that run.
    """
    focus_topic = body.focus_topic.strip() if body and body.focus_topic else ""
    return success_response(data=_run_data(start_compact_run(chat_id, focus_topic)), status_code=202)


@router.get("/{chat_id}/compact/runs/{run_id}", response_model=StandardSuccessResponse)
async def get_compaction_run(chat_id: str, run_id: str) -> JSONResponse:
    """A manual compaction of the chat by its id: running, or ended with its result or its failure.

    The server remembers only the newest runs and only until it restarts, so a run it does not hold is a 404.
    """
    run = get_compact_run(chat_id, run_id)
    if run is None:
        raise not_found_error("Compaction run")
    return success_response(data=_run_data(run))


@router.put("/{chat_id}/compaction/summary", response_model=StandardSuccessResponse)
async def update_compaction_summary(
    chat_id: str,
    body: UpdateSummaryRequest,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Update compaction summary for a chat session (admin override API)."""
    try:
        chat = await ChatService.get_chat_metadata(chat_id)
        if not chat:
            raise not_found_error("Chat session")

        await ChatService.update_compaction_summary(chat_id, body.summary)
        return success_response()
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(operation="Update compaction summary", exception=e) from e


@router.get("/{chat_id}/archive", response_model=StandardSuccessResponse)
async def get_chat_archive(
    chat_id: str,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Retrieve archived messages from compacted history (read-only, does not affect prefix cache)."""
    try:
        chat = await ChatService.get_chat_metadata(chat_id)
        if not chat:
            raise not_found_error("Chat session")

        from app.services.chat.compact_service import get_archived_messages

        messages = await get_archived_messages(chat_id)
        return success_response(data={"messages": messages})
    except HTTPException:
        raise
    except Exception as e:
        raise internal_error(operation="Get chat archive", exception=e) from e


class PinFilesRequest(BaseModel):
    files: list[str] = Field(default_factory=list)


class PinFileRequest(BaseModel):
    file_path: str = Field(min_length=1, max_length=1024)


class ContextBranchRequest(BaseModel):
    snapshot_path: str = Field(min_length=1, max_length=2048)
    label: str = Field(default="", max_length=120)


@router.get("/{chat_id}/context/pins", response_model=StandardSuccessResponse)
async def get_context_pins(chat_id: str) -> JSONResponse:
    from myrm_agent_harness.runtime.context.session.session_context_pins import (
        read_pinned_files,
    )

    chat = await ChatService.get_chat_metadata(chat_id)
    if not chat:
        raise not_found_error("Chat session")
    return success_response(data={"files": read_pinned_files(chat_id)})


@router.put("/{chat_id}/context/pins", response_model=StandardSuccessResponse)
async def set_context_pins(chat_id: str, body: PinFilesRequest) -> JSONResponse:
    from myrm_agent_harness.runtime.context.session.session_context_pins import (
        write_pinned_files,
    )

    chat = await ChatService.get_chat_metadata(chat_id)
    if not chat:
        raise not_found_error("Chat session")
    record = write_pinned_files(chat_id, body.files)
    return success_response(data={"files": list(record.files), "updated_at": record.updated_at})


@router.post("/{chat_id}/context/pins", response_model=StandardSuccessResponse)
async def add_context_pin(chat_id: str, body: PinFileRequest) -> JSONResponse:
    from myrm_agent_harness.runtime.context.session.session_context_pins import (
        add_pinned_file,
    )

    chat = await ChatService.get_chat_metadata(chat_id)
    if not chat:
        raise not_found_error("Chat session")
    record = add_pinned_file(chat_id, body.file_path)
    return success_response(data={"files": list(record.files), "updated_at": record.updated_at})


@router.get("/{chat_id}/context/branches", response_model=StandardSuccessResponse)
async def list_context_branches(chat_id: str) -> JSONResponse:
    from myrm_agent_harness.runtime.context.context_branches import list_context_branches as list_branches

    chat = await ChatService.get_chat_metadata(chat_id)
    if not chat:
        raise not_found_error("Chat session")
    branches = [
        {
            "branch_id": item.branch_id,
            "label": item.label,
            "snapshot_path": item.snapshot_path,
            "created_at": item.created_at,
        }
        for item in list_branches(chat_id)
    ]
    return success_response(data={"branches": branches})


@router.post("/{chat_id}/context/branches", response_model=StandardSuccessResponse)
async def create_context_branch(chat_id: str, body: ContextBranchRequest) -> JSONResponse:
    from myrm_agent_harness.runtime.context.context_branches import append_context_branch

    chat = await ChatService.get_chat_metadata(chat_id)
    if not chat:
        raise not_found_error("Chat session")
    record = append_context_branch(
        chat_id,
        snapshot_path=body.snapshot_path,
        label=body.label,
    )
    return success_response(
        data={
            "branch_id": record.branch_id,
            "label": record.label,
            "snapshot_path": record.snapshot_path,
            "created_at": record.created_at,
        }
    )


class BranchForkRequest(BaseModel):
    new_title: str = Field(default="", max_length=255)


@router.post("/{chat_id}/context/branches/{branch_id}/fork", response_model=StandardSuccessResponse)
async def fork_context_branch(
    chat_id: str,
    branch_id: str,
    body: BranchForkRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Fork a new chat from a snapshot bookmark (pre-compaction conversation restore)."""
    from app.services.chat.context_branch_fork import ContextBranchForkService

    chat = await ChatService.get_chat_metadata(chat_id)
    if not chat:
        raise not_found_error("Chat session")

    new_title = body.new_title.strip() if body and body.new_title else None
    result = await ContextBranchForkService.fork_from_branch(
        db,
        chat_id,
        branch_id,
        new_title=new_title,
    )
    if not result.success or result.new_chat_id is None:
        raise validation_error(result.error or "Failed to fork from snapshot bookmark")

    return success_response(
        data={
            "new_chat_id": result.new_chat_id,
            "parent_chat_id": result.parent_chat_id,
            "branch_id": result.branch_id,
            "message_count": result.message_count,
        }
    )
