"""Lossless context compaction service (public facade).

Stable import path for server callers. Implementation lives under ``compact/``.

[INPUT]
- compact.service, compact.persist, compact.history_change, compact.idle_estimate, compact.archive (POS: submodules)

[OUTPUT]
- Re-exports: compact_chat, persist_compaction, do_persist_to_db, tokens_replaced_by_summary,
  settle_compaction_after_history_change, CompactReason, CompactResult, idle estimate helpers, archive API

[POS]
Chat context compaction facade. Preserves ``app.services.chat.compact_service`` import path.
"""

from __future__ import annotations

from app.services.chat.compact._types import CompactReason, CompactResult
from app.services.chat.compact.archive import get_archived_messages
from app.services.chat.compact.history_change import settle_compaction_after_history_change
from app.services.chat.compact.idle_estimate import (
    estimate_compactable_context_tokens,
    estimate_idle_compact_request_tokens,
    resolve_idle_compact_token_floor,
    resolve_min_messages_to_compact,
)
from app.services.chat.compact.persist import (
    do_persist_to_db,
    is_compaction_failure_cooldown_active,
    persist_compaction,
)
from app.services.chat.compact.service import compact_chat, tokens_replaced_by_summary

__all__ = [
    "CompactReason",
    "CompactResult",
    "compact_chat",
    "do_persist_to_db",
    "estimate_compactable_context_tokens",
    "estimate_idle_compact_request_tokens",
    "get_archived_messages",
    "is_compaction_failure_cooldown_active",
    "persist_compaction",
    "resolve_idle_compact_token_floor",
    "resolve_min_messages_to_compact",
    "settle_compaction_after_history_change",
    "tokens_replaced_by_summary",
]
