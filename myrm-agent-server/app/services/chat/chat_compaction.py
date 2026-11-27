"""Compaction drain and summary update mixin.

[INPUT]
- _base::_ChatServiceBase (POS: repository 协议和访问器)
- conversation_recall_index_service::ConversationRecallIndexService (POS: Conversation Recall 索引生命周期服务)
- compact_service::do_persist_to_db (POS: 所有压缩写入者共用的落库入口)
- compact_service::tokens_replaced_by_summary (POS: 累计节省统计的统一口径：被新摘要取代的旧摘要与边界之后的消息)
- app.core.utils.time_utils::as_utc (POS: SQLite 无时区时间的 UTC 归一)
- app.core.channel_bridge.model_resolver::enrich_model_context_window (POS: 模型真实上下文窗口enricher)

[OUTPUT]
- _ChatCompactionMixin: compaction summary 更新、后台 drain 调度与执行

[POS]
Compaction drain 编排层。提供 compaction summary 字段更新、后台 drain 任务调度
和冷缓存离线摘要生成（含 LLM 调用；落库走共用的 `do_persist_to_db`，期间被回退删掉的边界会令摘要作废）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.utils.time_utils import as_utc
from app.database.repositories.uow import UnitOfWork

from ._base import _ChatServiceBase
from .compact_service import do_persist_to_db, tokens_replaced_by_summary
from .conversation_recall_index_service import ConversationRecallIndexService

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

    from app.database.dto import MessageDTO

logger = logging.getLogger(__name__)


def _to_langchain(rows: Sequence[MessageDTO]) -> list[BaseMessage]:
    """Rows of the active branch as the messages the summarizer reads, tool results included."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    messages: list[BaseMessage] = []
    for row in rows:
        if row.role == "user":
            messages.append(HumanMessage(content=row.content))
        elif row.role == "assistant":
            messages.append(AIMessage(content=row.content))
        elif row.role == "tool":
            tool_call_id = row.extra_data.get("tool_call_id", "") if row.extra_data else ""
            messages.append(ToolMessage(content=row.content, tool_call_id=str(tool_call_id)))
    return messages


class _ChatCompactionMixin(_ChatServiceBase):
    """Compaction drain and summary operations."""

    @staticmethod
    async def update_compaction_summary(chat_id: str, summary: str) -> None:
        async with UnitOfWork() as uow:
            await _ChatServiceBase._cr(uow).update_chat_fields(chat_id, {"compacted_summary": summary})
            sess = uow.session
            assert sess is not None
            await sess.flush()
            await ConversationRecallIndexService.rebuild_chat(sess, chat_id)

    @staticmethod
    def schedule_background_drain(chat_id: str) -> None:
        """Schedule a background task to drain compaction debt (cold cache drain)"""
        import asyncio

        asyncio.create_task(_ChatCompactionMixin.flush_compaction_debt(chat_id))

    @staticmethod
    async def flush_compaction_debt(chat_id: str) -> None:
        """Background Drain Worker: summarize the chat once its prompt cache is cold and store it through the shared writer."""
        import asyncio

        # 1. Wait for cache to get cold
        await asyncio.sleep(300)

        try:
            async with UnitOfWork() as uow:
                latest_msg = await _ChatServiceBase._cr(uow).get_latest_message(chat_id)
                if not latest_msg:
                    return
                snapshot_id = latest_msg.id

                chat = await _ChatServiceBase._cr(uow).get_chat_by_id(chat_id, load_messages=False)
                if not chat:
                    return

                age_seconds = (datetime.now(UTC) - as_utc(latest_msg.created_at)).total_seconds()
                if age_seconds < 290:
                    logger.info("❄️ [Drain] Cache still hot for %s, deferring drain.", chat_id)
                    return

            logger.info("❄️ [Drain] Cache is cold for %s, starting offline summarization for snapshot %s.", chat_id, snapshot_id)

            async with UnitOfWork() as uow:
                all_msgs = await _ChatServiceBase._cr(uow).get_all_messages(chat_id)

            target_msgs = []
            for msg in all_msgs:
                target_msgs.append(msg)
                if msg.id == snapshot_id:
                    break

            langchain_msgs = _to_langchain(target_msgs)

            from myrm_agent_harness.agent.context_management.infra.schemas import (
                ContextConfig,
            )
            from myrm_agent_harness.agent.context_management.strategies.summary.summarizer import (
                generate_structured_summary,
            )
            from myrm_agent_harness.toolkits.llms import llm_manager
            from myrm_agent_harness.utils.text_utils import get_token_count

            from app.core.channel_bridge.config_loader import load_user_configs

            configs = await load_user_configs()
            providers_dict = configs.providers_dict if configs else None

            model_cfg = None
            if providers_dict:
                from app.services.agent.params.model_config import _resolve_model_config
                from app.services.agent.params.models import ModelSelection

                default_model_cfg = providers_dict.get("defaultModelConfig", {})
                if isinstance(default_model_cfg, dict):
                    lite_model = default_model_cfg.get("liteModel") or {}
                    selection = lite_model.get("primary") or lite_model.get("selection")
                    if selection and isinstance(selection, dict):
                        provider_id = selection.get("providerId")
                        model = selection.get("model")
                        if provider_id and model:
                            ms = ModelSelection(provider_id=str(provider_id), model=str(model))
                            try:
                                model_cfg = await _resolve_model_config(ms, providers_dict)
                            except Exception:
                                pass
            if not model_cfg:
                from app.core.channel_bridge.model_resolver import resolve_model_config

                model_cfg = resolve_model_config(providers_dict)

            from app.core.channel_bridge.model_resolver import enrich_model_context_window

            model_cfg = enrich_model_context_window(model_cfg, providers_dict)

            llm = await llm_manager.get_llm_from_config(model_cfg, api_keys=getattr(model_cfg, "api_keys", None))

            context_config = ContextConfig(max_context_tokens=model_cfg.max_context_tokens or 128000)
            _, summary = await generate_structured_summary(langchain_msgs, llm, chat_id, config=context_config)

            summary_json = summary.to_json()
            # Measured as compact_chat measures it: the rows the stored summary already covers were counted when it was
            # stored, and counting them again would inflate the running total.
            boundary_index = next((i for i, row in enumerate(target_msgs) if row.id == chat.compacted_before_id), -1)
            replaced = tokens_replaced_by_summary(_to_langchain(target_msgs[boundary_index + 1 :]), chat.compacted_summary)
            tokens_saved = max(replaced - get_token_count(summary_json), 0)

            async with UnitOfWork() as uow:
                session = uow.session
                assert session is not None
                try:
                    # The writer every compaction shares: it takes the write lock first, then refuses a boundary a
                    # rewind or undo removed while the model ran, and keeps a newer boundary another writer stored.
                    await do_persist_to_db(session, chat_id, summary_json, snapshot_id, tokens_saved)
                except ValueError as exc:
                    logger.info("⚠️ [Drain] Summary for chat %s discarded: %s", chat_id, exc)
                    return
                # Committed before the archive below: a failing archive must not void the summary already paid for.
                await uow.commit()

                # PreCompact Hook: archive salient tool verbatim outputs into recall index. After the persist, whose
                # index rebuild drops every segment no message backs.
                try:
                    from app.services.chat.salient_tool_archive_service import SalientToolArchiveService

                    await SalientToolArchiveService().archive_salient_tools(
                        session,
                        chat_id=chat_id,
                        messages=langchain_msgs,
                    )
                except Exception as archive_err:
                    logger.warning("[Drain] Salient tool archival failed (non-blocking): %s", archive_err)

                logger.info("✅ [Drain] Compaction stored for chat %s, boundary: %s", chat_id, snapshot_id)

        except Exception:
            logger.exception("❌ [Drain] Background drain failed for %s", chat_id)
