"""Post-compaction active file reread processor.

After compaction (Compress/SessionNotes/Summarize), the model loses byte-level
visibility of file contents it was previously working on, so its next edit
would need a re-read first, which costs 1-5 tool-call round-trips.

This processor reads the top-N recently modified/created files from
ArtifactTracker and appends their content as one HumanMessage at the tail of
the request, eliminating those redundant tool calls.

Design choices:
- Triggered only by the savings the compaction processors made themselves
  (ProcessorContext.tokens_saved_by): thinking cleanup, media filtering and
  TTL pruning leave file text readable, so a reread after them is pure cost.
- Appended as HumanMessage (not SystemMessage) to preserve system prompt cache.
- Dynamic token budget: min(50k, 25% of the window left), floor 5k; the window
  is the one the processor was built with.
- Single-file cap: 40% of budget prevents one large file from starving others.
- A path is opened and redacted the way the file tools open and show it, so
  the reread and a normal file read agree on the file and on its text.
- Graceful degradation: on any failure, the pipeline continues unchanged.

[INPUT]
- tracking.artifact_tracker::get_artifact_tracker, ArtifactAction (POS: session artifact tracking)
- utils.text_utils::get_token_count (POS: token counting)
- core.context_vars::workspace_root_var (POS: the run's workspace root)
- core.security.path.typed_path::opened_path (POS: the one definition of which file a typed path opens)
- core.security.redact::redact_file_text (POS: per-file redaction shared with the file tools)
- pipeline.base::BaseProcessor, ProcessorContext (POS: processor interface)

[OUTPUT]
- PostCompactionRereadProcessor: class — post-compaction file reread processor

[POS]
Post-compaction active file reread. Reads recently modified files and appends
their content at the request tail after compaction, saving tool-call round-trips.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, HumanMessage

from myrm_agent_harness.core.context_vars import workspace_root_var
from myrm_agent_harness.core.security.path.typed_path import opened_path
from myrm_agent_harness.core.security.redact import redact_file_text
from myrm_agent_harness.utils.logger_utils import get_agent_logger
from myrm_agent_harness.utils.text_utils import get_token_count

from ..base import BaseProcessor, ProcessorContext

if TYPE_CHECKING:
    from myrm_agent_harness.agent.context_management.tracking.artifact_tracker import (
        ArtifactRecord,
    )

logger = get_agent_logger(__name__)

_MAX_FILES = 5
_MAX_BUDGET_TOKENS = 50_000
_BUDGET_RATIO = 0.25
_MIN_BUDGET_TOKENS = 5_000
_SINGLE_FILE_BUDGET_RATIO = 0.40

# The processors that rewrite history. The tests hold these names equal to the default chain's processor names.
_COMPACTION_PROCESSOR_NAMES = frozenset({"compress", "session_notes", "summarize"})


class PostCompactionRereadProcessor(BaseProcessor):
    """Re-reads recently touched files after context compaction.

    Only activates when a compaction processor itself saved tokens and a chat_id is
    available to query ArtifactTracker.
    """

    def __init__(self, max_context_tokens: int = 128000) -> None:
        self._max_context_tokens = max_context_tokens

    @property
    def name(self) -> str:
        return "post_compaction_reread"

    async def should_process(self, context: ProcessorContext) -> bool:
        if context.chat_id is None:
            return False
        return any(context.tokens_saved_by.get(name, 0) > 0 for name in _COMPACTION_PROCESSOR_NAMES)

    async def process(self, context: ProcessorContext) -> ProcessorContext:
        chat_id = context.chat_id
        if not chat_id:
            return context

        from myrm_agent_harness.agent.context_management.tracking.artifact_tracker import (
            get_artifact_tracker,
        )

        tracker = get_artifact_tracker(chat_id)
        if not tracker:
            return context

        candidates = _select_recent_files(tracker.records, _MAX_FILES)
        if not candidates:
            logger.debug("[PostCompactionReread] No recent files to re-read")
            return context

        budget = _compute_budget(self._max_context_tokens, context.messages)
        if budget < _MIN_BUDGET_TOKENS:
            logger.debug("[PostCompactionReread] Budget too small (%d), skipping", budget)
            return context

        single_file_cap = int(budget * _SINGLE_FILE_BUDGET_RATIO)
        parts: list[str] = []
        total_tokens = 0
        reread_paths: list[str] = []

        for path in candidates:
            content = _safe_read_file(path)
            if content is None:
                continue

            file_tokens = get_token_count(content)
            if file_tokens > single_file_cap:
                content = _truncate_head_tail(content, single_file_cap)
                file_tokens = single_file_cap

            if total_tokens + file_tokens > budget:
                break

            parts.append(f"--- {path} ---")
            parts.append(content)
            parts.append("")
            total_tokens += file_tokens
            reread_paths.append(path)

        if not parts:
            return context

        header = (
            "[System note: The following file contents were automatically re-loaded "
            "after context compaction. They reflect the latest on-disk state of files "
            "you were recently working on. No need to re-read them with file_read_tool.]"
        )
        injection = "\n".join(["<post-compaction-reread>", header, "", *parts, "</post-compaction-reread>"])

        reread_msg = HumanMessage(content=injection)
        context.messages.append(reread_msg)

        logger.info(
            "[PostCompactionReread] Injected %d files (~%d tokens): %s",
            len(reread_paths),
            total_tokens,
            reread_paths,
        )
        return context


def _select_recent_files(
    records: list[ArtifactRecord],
    max_files: int,
) -> list[str]:
    """Select the most recently created/modified files, newest first."""
    from myrm_agent_harness.agent.context_management.tracking.artifact_tracker import (
        ArtifactAction,
    )

    path_latest: dict[str, float] = {}
    deleted: set[str] = set()

    for record in records:
        if record.action == ArtifactAction.DELETED:
            deleted.add(record.path)
            continue
        if record.action in (ArtifactAction.CREATED, ArtifactAction.MODIFIED):
            ts = record.timestamp.timestamp()
            if record.path not in path_latest or ts > path_latest[record.path]:
                path_latest[record.path] = ts

    for d in deleted:
        path_latest.pop(d, None)

    sorted_paths = sorted(path_latest, key=lambda p: path_latest[p], reverse=True)
    return sorted_paths[:max_files]


def _compute_budget(max_context_tokens: int, messages: list[BaseMessage]) -> int:
    """Compute the token budget for reread injection: a quarter of the window left, within the floor and the cap."""
    current_tokens = sum(get_token_count(msg.content) for msg in messages if isinstance(msg.content, str))
    remaining = max(0, max_context_tokens - current_tokens)
    proportional = int(remaining * _BUDGET_RATIO)
    return max(_MIN_BUDGET_TOKENS, min(_MAX_BUDGET_TOKENS, proportional))


def _truncate_head_tail(content: str, token_budget: int) -> str:
    """Keep head and tail portions within budget."""
    lines = content.splitlines(keepends=True)
    head_budget = int(token_budget * 0.6)
    tail_budget = token_budget - head_budget

    head_lines: list[str] = []
    head_tokens = 0
    for line in lines:
        lt = get_token_count(line)
        if head_tokens + lt > head_budget:
            break
        head_lines.append(line)
        head_tokens += lt

    tail_lines: list[str] = []
    tail_tokens = 0
    for line in reversed(lines):
        lt = get_token_count(line)
        if tail_tokens + lt > tail_budget:
            break
        tail_lines.insert(0, line)
        tail_tokens += lt

    if not head_lines and not tail_lines:
        return content[:500] + "\n... [truncated] ..."

    omitted = len(lines) - len(head_lines) - len(tail_lines)
    if omitted <= 0:
        return content

    return "".join(head_lines) + f"\n... [{omitted} lines omitted] ...\n" + "".join(tail_lines)


def _safe_read_file(path: str) -> str | None:
    """Read the file the file tools open for ``path``, masked the way they show it; None on any failure."""
    try:
        opened = opened_path(path, workspace_root_var.get().strip() or None)
        if opened is None or not os.path.isfile(opened):
            return None
        with open(opened, encoding="utf-8", errors="replace") as f:
            return redact_file_text(path, f.read())
    except Exception:
        logger.debug("[PostCompactionReread] Failed to read %s", path, exc_info=True)
        return None
