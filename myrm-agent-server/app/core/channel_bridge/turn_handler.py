"""RetryHandler / UndoHandler implementations for IM /retry and /undo commands.

[INPUT]
- app.channels.protocols.turn_management::RetryHandler, RetryResult, UndoHandler, UndoResult
- app.channels.types::InboundMessage
- app.core.channel_bridge.command_session::resolve_command_session_key (POS: the session key of the conversation a command acts on)
- app.services.chat.chat_service::ChatService
- app.services.files.revert_hydrate::cleanup_persisted_snapshots (POS: Server-side revert snapshot disk hydrate and cleanup)
- myrm_agent_harness.agent.meta_tools.file_ops.revert_service::RevertService

[OUTPUT]
- ChannelRetryHandler: RetryHandler 业务实现
- ChannelUndoHandler: UndoHandler 业务实现
- _current_chat_id: chat holding the conversation a command acts on, None when nothing was stored there yet
- _revert_deleted_turn: restore the files a deleted turn changed, with the counts
- _revert_messages: best-effort file revert + restore_inbox notify

[POS]
将框架层的 RetryHandler / UndoHandler 协议映射到 ChatService 的
retry_last_turn / undo_last_turn 方法，并联动 RevertService 还原关联文件快照。
命令作用于 `resolve_command_session_key` 解出的会话；会话里还没存过回合（含 `/new` 刚开出的新会话），命令照「没有可操作的回合」处理。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from myrm_agent_harness.agent.meta_tools.file_ops.revert_service import RevertService

from app.channels.protocols.turn_management import RetryResult, UndoResult
from app.channels.types import InboundMessage
from app.core.channel_bridge.command_session import resolve_command_session_key
from app.services.chat.chat_service import ChatService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RevertMessagesOutcome:
    reverted_count: int
    not_revertible_count: int


async def _revert_messages(session_id: str, message_ids: list[str]) -> RevertMessagesOutcome:
    """Best-effort revert file snapshots for deleted messages."""
    from app.services.files.revert_hydrate import (
        cleanup_persisted_snapshots,
        ensure_session_snapshots_hydrated,
    )

    await ensure_session_snapshots_hydrated(session_id)

    reverted_total = 0
    not_revertible_total = 0
    reverted_paths: list[str] = []
    for mid in message_ids:
        try:
            changes = await RevertService.get_message_changes(session_id, mid)
            not_revertible_paths = {c.path for c in changes if not c.revertible}
            not_revertible_total += len(not_revertible_paths)

            result = await RevertService.revert_message(session_id, mid)
            reverted_total += len(result.reverted_files)
            if result.reverted_files:
                reverted_paths.extend(result.reverted_files)
                await cleanup_persisted_snapshots(session_id, mid)
            if result.warnings:
                logger.debug("Revert warnings for message %s: %s", mid, result.warnings)
        except Exception:
            logger.warning("Failed to revert snapshots for message %s", mid, exc_info=True)

    if reverted_paths:
        from app.services.files.revert_agent_notify import notify_agent_of_turn_revert

        notify_agent_of_turn_revert(
            session_id=session_id,
            message_id=None,
            reverted_files=list(dict.fromkeys(reverted_paths)),
        )

    return RevertMessagesOutcome(
        reverted_count=reverted_total,
        not_revertible_count=not_revertible_total,
    )


_NOTHING_REVERTED = RevertMessagesOutcome(reverted_count=0, not_revertible_count=0)


async def _current_chat_id(msg: InboundMessage) -> str | None:
    """The chat holding ``msg``'s conversation; ``None`` when nothing was stored there yet."""
    chat = await ChatService.get_channel_chat_by_key(await resolve_command_session_key(msg))
    return chat.id if chat else None


async def _revert_deleted_turn(handler: str, chat_id: str, deleted_message_ids: list[str]) -> RevertMessagesOutcome:
    """Restore the files the deleted turn changed, and say how many came back."""
    if not deleted_message_ids:
        return _NOTHING_REVERTED
    outcome = await _revert_messages(chat_id, deleted_message_ids)
    if outcome.reverted_count:
        logger.info("%s: reverted %d files for chat %s", handler, outcome.reverted_count, chat_id)
    return outcome


class ChannelRetryHandler:
    """Business-layer RetryHandler for /retry command."""

    async def __call__(self, msg: InboundMessage, user_id: str) -> RetryResult:
        chat_id = await _current_chat_id(msg)
        if chat_id is None:
            return RetryResult(success=False)
        svc_result = await ChatService.retry_last_turn(chat_id)

        reverted = (
            await _revert_deleted_turn("ChannelRetryHandler", chat_id, svc_result.deleted_message_ids)
            if svc_result.success
            else _NOTHING_REVERTED
        )
        return RetryResult(
            success=svc_result.success,
            query=svc_result.query,
            deleted_count=svc_result.deleted_count,
            reverted_count=reverted.reverted_count,
            files_not_revertible=reverted.not_revertible_count,
        )


class ChannelUndoHandler:
    """Business-layer UndoHandler for /undo command."""

    async def __call__(self, msg: InboundMessage, user_id: str) -> UndoResult:
        chat_id = await _current_chat_id(msg)
        if chat_id is None:
            return UndoResult(success=False)
        svc_result = await ChatService.undo_last_turn(chat_id)

        reverted = (
            await _revert_deleted_turn("ChannelUndoHandler", chat_id, svc_result.deleted_message_ids)
            if svc_result.success
            else _NOTHING_REVERTED
        )
        return UndoResult(
            success=svc_result.success,
            deleted_count=svc_result.deleted_count,
            reverted_count=reverted.reverted_count,
            files_not_revertible=reverted.not_revertible_count,
        )
