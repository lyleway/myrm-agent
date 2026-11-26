"""
[INPUT]
- app.database.models.approval::ApprovalRecord (POS: 审批持久化模型)
- app.services.event.app_event_bus::AppEvent (POS: 服务器级 SSE 总线)
- app.database.factory::acquire_write_lock (POS: 事务起始处获取 SQLite 写锁，供读后写的工作单元使用)
- app.services.approvals.outbound_draft::finalize_outbound_draft (POS: 被扣留回复定案后的发送/释放/收尾)
- app.services.approvals.chat_peer_card::push_approval_card_to_chat_peer (POS: 把审批卡推给 IM 会话的对端)
- app.services.approvals.external_agent_permission::ACTION_TYPE, pending_permission_answers (POS: 外部 Agent 权限请求的决定交还给等待它的回合)
- app.services.skills.growth.constants::GROWTH_ACTION_TYPES, is_background_growth_approval (POS: growth draft 分流 SSOT)
- app.core.notifications.approval_card::build_approval_card (POS: 审批卡文案，按发送路由决定是否带按钮)
- app.core.channel_bridge.locale_provider::resolve_user_locale (POS: 所有者的语言偏好)

[OUTPUT]
- ApprovalRegistry: 统一的拦截审批注册与唤醒中枢
- list_pending_growth: PENDING 背景成长草稿查询（去重/上限 SSOT）
- find_pending_outbound_drafts: 按 ID 前缀查找待审核的被扣留回复（/draft 命令入口，仅 outbound_draft）

[POS]
统一审批流调度器。负责将各种拦截节点落库并推送 SSE 事件，接收 resolve 指令。
待审批记录的会话来自 IM 时，还会把审批卡推给该会话的对端（与通知分发器同一张卡：所有者语言，按钮只给点得动的路由，不带请求载荷）；
outbound_draft 不推：它只由所有者决定，所有者收到的是通知分发器的卡，会话对端已有「待审核」占位。
outbound_draft 的定案（WebUI/IM 按钮/`/draft`/批量/TTL）统一在此收口：状态提交后调用 finalize_outbound_draft。
外部 Agent 的权限请求（external_agent_permission）同样在此收口：任何入口决定后，把裁决交给等待它的回合并广播 APPROVAL_RESOLVED，
让别的设备上打开的审批卡关闭；回合先结束（停止、超时）的请求由 withdraw 关闭。
定案是读后写（resolve_approval、TTL 清理），事务起始先取 SQLite 写锁：并发的定案只有一方生效，另一方等待后读到已定案状态
（resolve_approval 返回 None），不会重复发送；等锁最多一个 busy_timeout，超时才以 "database is locked" 失败。续跑时结算的浏览器接管审批同样逐条走 resolve_approval。
"""

import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import ColumnElement, and_, func, or_, select

from app.database.connection import get_session
from app.database.factory import acquire_write_lock
from app.database.models.approval import ApprovalRecord
from app.services.approvals.chat_peer_card import push_approval_card_to_chat_peer
from app.services.approvals.external_agent_permission import ACTION_TYPE as EXTERNAL_AGENT_PERMISSION
from app.services.approvals.external_agent_permission import (
    DECISION_NOTE_CHARS,
    DECISION_NOTE_KEY,
    pending_permission_answers,
)
from app.services.approvals.outbound_draft import finalize_outbound_draft
from app.services.event.app_event_bus import AppEvent, AppEventType, get_event_bus
from app.services.skills.growth.constants import GROWTH_ACTION_TYPES, is_background_growth_approval

logger = logging.getLogger(__name__)

_OUTBOUND_DRAFT = "outbound_draft"


class ApprovalRegistry:
    """Registry for managing execution interruptions and manual approvals."""

    @classmethod
    async def cleanup_expired_approvals(cls) -> int:
        """Settle pending approvals that have expired: TIMEOUT, or APPROVED for a held reply set to auto_send.

        Finalizes the stored state and re-syncs the pending count of the chats it touched.
        A suspended agent is resumed by the harness ApprovalTimeoutScheduler when its timer fires, not by this sweep.

        Returns:
            The number of expired approvals settled.
        """
        async with get_session() as db:
            await acquire_write_lock(db)  # as in resolve_approval: a click racing the sweep decides the record only once
            stmt = select(ApprovalRecord).where(
                and_(
                    ApprovalRecord.status == "PENDING",
                    ApprovalRecord.expires_at.is_not(None),
                    ApprovalRecord.expires_at <= datetime.now(timezone.utc),
                )
            )
            result = await db.execute(stmt)
            expired_records = result.scalars().all()

            if not expired_records:
                return 0

            expired_drafts: list[ApprovalRecord] = []
            expired_permissions: list[ApprovalRecord] = []

            for record in expired_records:
                record.resolved_at = datetime.now(timezone.utc)

                if record.action_type == _OUTBOUND_DRAFT:
                    timeout_action = (record.payload or {}).get("draft_timeout_action", "auto_reject")
                    if timeout_action == "auto_send":
                        record.status = "APPROVED"
                        record.reason = (record.reason or "") + "\n\n[Auto-Sent: TTL Expired]"
                    else:
                        record.status = "TIMEOUT"
                        record.reason = (record.reason or "") + "\n\n[Auto-Rejected: TTL Expired]"
                    expired_drafts.append(record)
                    continue

                record.status = "TIMEOUT"
                record.reason = (record.reason or "") + "\n\n[Auto-Rejected: TTL Expired]"
                if record.action_type == EXTERNAL_AGENT_PERMISSION:
                    expired_permissions.append(record)

            chat_ids = {record.chat_id for record in expired_records}
            await db.commit()

        for record in expired_permissions:
            # The turn waiting on it is refused, and the cards of it close.
            pending_permission_answers.settle(record)
            cls._publish_resolved(record, action="expired")

        # Neither step raises; running them together keeps one slow platform call from holding up the rest.
        await asyncio.gather(
            *(finalize_outbound_draft(record) for record in expired_drafts),
            *(cls._sync_copilot_pending(chat_id) for chat_id in chat_ids),
        )

        return len(expired_records)

    @classmethod
    async def create_approval(
        cls,
        agent_id: str,
        action_type: str,
        payload: dict[str, object],
        reason: str | None = None,
        severity: str = "warning",
        chat_id: str | None = None,
        thread_id: str | None = None,
        status: str = "PENDING",
        expires_at: datetime | None = None,
    ) -> ApprovalRecord:
        """Create a new approval record and emit an SSE event."""
        record_id = uuid4().hex

        async with get_session() as db:
            record = ApprovalRecord(
                id=record_id,
                agent_id=agent_id,
                chat_id=chat_id,
                thread_id=thread_id,
                action_type=action_type,
                reason=reason,
                severity=severity,
                payload=payload,
                status=status,
                expires_at=expires_at,
            )
            db.add(record)
            await db.commit()
            await db.refresh(record)

        # Broadcast SSE. Background growth drafts never emit from here:
        # draft_notification owns SKILL_GROWTH_UPDATED / NEW_SKILL_DRAFT for them.
        try:
            bus = get_event_bus()
            event_type: AppEventType | None
            if is_background_growth_approval(action_type, thread_id):
                event_type = None
            elif status == "PENDING":
                event_type = AppEventType.APPROVAL_REQUIRED
            else:
                event_type = AppEventType.SKILL_GROWTH_UPDATED

            if event_type is not None:
                bus.publish(
                    AppEvent(
                        event_type=event_type,
                        data={
                            "approval_id": record.id,
                            "action_type": action_type,
                            "status": status,
                            "severity": severity,
                            "reason": reason,
                            "payload": payload,
                            "chat_id": chat_id,
                            "expires_at": expires_at.isoformat() if expires_at else None,
                        },
                    )
                )
            logger.info("Approval record created and published: id=%s type=%s", record.id, action_type)
        except Exception as e:
            logger.error("Failed to publish approval event: %s", e)

        if chat_id and status == "PENDING" and action_type != _OUTBOUND_DRAFT:
            await push_approval_card_to_chat_peer(record, chat_id)

        await cls._sync_copilot_pending(chat_id)
        return record

    @classmethod
    async def resolve_approval(
        cls,
        approval_id: str,
        decision: str,
        edited_payload: dict[str, object] | None = None,
        comment: str | None = None,
    ) -> ApprovalRecord | None:
        """Resolve an approval and return the record for further processing.

        Args:
            decision: 'approve' or 'deny'
            comment: why the person decided as they did; kept on an external agent's permission request so a refusal can
                tell the agent why. Other approvals have nowhere to put it.
        """
        async with get_session() as db:
            # Check-then-update on one record: with the write lock taken first, a resolver racing this one waits
            # here and then finds the record decided (None), instead of both deciding it or the loser failing
            # with "database is locked" once the winner commits.
            await acquire_write_lock(db)
            stmt = select(ApprovalRecord).where(ApprovalRecord.id == approval_id)
            result = await db.execute(stmt)
            record = result.scalar_one_or_none()

            if not record:
                return None

            if record.status != "PENDING":
                return None

            record.status = "APPROVED" if decision == "approve" else "REJECTED"
            record.resolved_at = datetime.now(timezone.utc)

            if edited_payload:
                # Merge the edited fields into the original payload
                new_payload = dict(record.payload)
                new_payload.update(edited_payload)
                record.payload = new_payload

            note = (comment or "").strip()[:DECISION_NOTE_CHARS]
            if note and record.action_type == EXTERNAL_AGENT_PERMISSION:
                record.payload = {**record.payload, DECISION_NOTE_KEY: note}

            await db.commit()
            await db.refresh(record)

        if record.action_type == _OUTBOUND_DRAFT:
            # Here rather than in each resolver (WebUI, IM button, ``/draft``, batch), so every decision on a held
            # reply sends or releases it and closes the sender's placeholder, whichever door it came through.
            # After the session is released: sending talks to the channel and must not hold a connection.
            await finalize_outbound_draft(record)
        elif record.action_type == EXTERNAL_AGENT_PERMISSION:
            # The turn waiting on the request carries on with the decision, whichever door it came through.
            pending_permission_answers.settle(record)
            cls._publish_resolved(record, action="decided")
        await cls._sync_copilot_pending(record.chat_id)
        return record

    @classmethod
    async def withdraw(cls, approval_id: str, *, status: str, note: str) -> bool:
        """Close a pending approval nobody decided because the request behind it is over (its turn stopped or timed out).

        Returns False when it was already decided. Open cards of it close on every device.
        """
        return bool(await cls._withdraw_pending(ApprovalRecord.id == approval_id, status=status, note=note))

    @classmethod
    async def withdraw_pending_of_type(cls, action_type: str, *, status: str, note: str) -> int:
        """Close every pending approval of ``action_type``: the requests whose asker is gone (a restart ended its turn)."""
        return await cls._withdraw_pending(ApprovalRecord.action_type == action_type, status=status, note=note)

    @classmethod
    async def _withdraw_pending(cls, condition: ColumnElement[bool], *, status: str, note: str) -> int:
        async with get_session() as db:
            await acquire_write_lock(db)  # a decision racing the withdrawal settles the record only once
            records = (
                (await db.execute(select(ApprovalRecord).where(and_(ApprovalRecord.status == "PENDING", condition))))
                .scalars()
                .all()
            )
            for record in records:
                record.status = status
                record.resolved_at = datetime.now(timezone.utc)
                record.reason = f"{record.reason or ''}\n\n[{note}]"
            await db.commit()
        for record in records:
            cls._publish_resolved(record, action="withdrawn")
        for chat_id in {record.chat_id for record in records}:
            await cls._sync_copilot_pending(chat_id)
        return len(records)

    @classmethod
    def _publish_resolved(cls, record: ApprovalRecord, *, action: str) -> None:
        """Tell every open surface that ``record`` is settled, so a card of it stops asking."""
        try:
            get_event_bus().publish(
                AppEvent(
                    event_type=AppEventType.APPROVAL_RESOLVED,
                    data={
                        "action": action,
                        "approval_id": record.id,
                        "chat_id": record.chat_id,
                        "agent_id": record.agent_id,
                        "decision": record.status,
                    },
                )
            )
        except Exception as e:
            logger.error("Failed to publish approval resolution: %s", e)

    @classmethod
    async def get_approval(cls, approval_id: str) -> ApprovalRecord | None:
        """Fetch an approval record by its ID."""
        async with get_session() as db:
            stmt = select(ApprovalRecord).where(ApprovalRecord.id == approval_id)
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    @classmethod
    async def find_pending_outbound_drafts(cls, id_prefix: str) -> list[ApprovalRecord]:
        """Held replies still awaiting a decision whose id starts with ``id_prefix`` (how ``/draft`` finds one).

        At most two are returned, which is all it takes to tell a unique match from an ambiguous one. Only held
        replies match, so a code typed in chat can never decide any other kind of approval.
        """
        async with get_session() as db:
            stmt = (
                select(ApprovalRecord)
                .where(ApprovalRecord.status == "PENDING")
                .where(ApprovalRecord.action_type == _OUTBOUND_DRAFT)
                .where(ApprovalRecord.id.startswith(id_prefix, autoescape=True))
                .limit(2)
            )
            result = await db.execute(stmt)
            return list(result.scalars().all())

    # SQL form of is_background_growth_approval (keep the two in step). Background growth drafts live in the /skills/drafts
    # inbox, so the pending count and the approvals list leave them out while list_pending_growth returns only them.
    _BACKGROUND_GROWTH = and_(
        ApprovalRecord.action_type.in_(GROWTH_ACTION_TYPES),
        or_(ApprovalRecord.thread_id.is_(None), ApprovalRecord.thread_id == ""),
    )

    @classmethod
    async def count_pending_for_chat(cls, chat_id: str) -> int:
        if not chat_id.strip():
            return 0
        async with get_session() as db:
            stmt = (
                select(func.count())
                .select_from(ApprovalRecord)
                .where(ApprovalRecord.status == "PENDING")
                .where(ApprovalRecord.chat_id == chat_id)
                .where(~cls._BACKGROUND_GROWTH)
            )
            result = await db.execute(stmt)
            return int(result.scalar_one())

    @classmethod
    async def _sync_copilot_pending(cls, chat_id: str | None) -> None:
        if not chat_id:
            return
        try:
            from app.services.copilot.run_digest_store import RunDigestStore

            count = await cls.count_pending_for_chat(chat_id)
            RunDigestStore.set_pending_approval_count(chat_id, count)
        except Exception as exc:
            logger.warning("Failed to sync copilot pending approval count: %s", exc)

    @classmethod
    async def list_pending(cls, limit: int = 50, offset: int = 0) -> list[ApprovalRecord]:
        async with get_session() as db:
            stmt = (
                select(ApprovalRecord)
                .where(ApprovalRecord.status == "PENDING")
                .where(~cls._BACKGROUND_GROWTH)
                .order_by(ApprovalRecord.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await db.execute(stmt)
            return list(result.scalars().all())

    @classmethod
    async def list_pending_growth(cls, limit: int = 50, offset: int = 0) -> list[ApprovalRecord]:
        """List PENDING background-growth drafts (hidden from the global approvals inbox).

        `list_pending` intentionally excludes background growth drafts (skill_draft /
        skill_patch / semantic_memory without a thread_id) because they live in the
        /skills/drafts inbox. `persist_skill_draft_record` uses this to enforce
        deduplication and the pending-proposal cap on those same drafts.
        """
        async with get_session() as db:
            stmt = (
                select(ApprovalRecord)
                .where(ApprovalRecord.status == "PENDING")
                .where(cls._BACKGROUND_GROWTH)
                .order_by(ApprovalRecord.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await db.execute(stmt)
            return list(result.scalars().all())

    @classmethod
    async def resolve_pending_browser_takeover_for_chat(
        cls,
        chat_id: str,
        *,
        decision: str = "approve",
    ) -> int:
        """Resolve orphan browser_takeover approvals after HITL resume via chat stream."""
        if not chat_id.strip():
            return 0
        normalized = "approve" if decision == "approve" else "deny"
        async with get_session() as db:
            stmt = select(ApprovalRecord.id).where(
                and_(
                    ApprovalRecord.chat_id == chat_id,
                    ApprovalRecord.status == "PENDING",
                    ApprovalRecord.action_type == "browser_takeover",
                )
            )
            approval_ids = list((await db.execute(stmt)).scalars().all())
        # Each one is decided by resolve_approval like any other approval: it takes the write lock first, so a
        # concurrent writer only delays this request (up to busy_timeout), and it refreshes the run digest's pending count.
        resolved_count = 0
        for approval_id in approval_ids:
            if await cls.resolve_approval(approval_id, normalized) is not None:
                resolved_count += 1
        if resolved_count:
            logger.info(
                "Resolved %s pending browser_takeover approval(s) for chat_id=%s decision=%s",
                resolved_count,
                chat_id,
                normalized,
            )
        return resolved_count
