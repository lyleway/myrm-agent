"""Shared constants for chat context compaction.

[INPUT]
(none — leaf constants module)

[OUTPUT]
- MIN_MESSAGES_TO_COMPACT, MIN_INCREMENTAL_MESSAGES_TO_COMPACT: Thresholds
- IDLE_SUMMARY_TARGET_RATIO, COMPACTION_FAILURE_COOLDOWN_SECONDS: Tuning
- DEFAULT_TOOL_SCHEMA_TOKEN_RESERVE, MAX_TOOL_OVERHEAD_TOKENS: Token budgets
- COMPACT_INACTIVITY_TIMEOUT_S, COMPACT_TOTAL_CEILING_S: Timeout guards
- COMPACT_RUN_DRAIN_TIMEOUT_S: How long shutdown waits for a manual compaction run in flight

[POS]
Compaction 子包共享常量。被 service.py、idle_estimate.py、run.py 等引用。
"""

from __future__ import annotations

MIN_MESSAGES_TO_COMPACT = 10
MIN_INCREMENTAL_MESSAGES_TO_COMPACT = 2
IDLE_SUMMARY_TARGET_RATIO = 0.20
COMPACTION_FAILURE_COOLDOWN_SECONDS = 300
DEFAULT_TOOL_SCHEMA_TOKEN_RESERVE = 8_000
MAX_TOOL_OVERHEAD_TOKENS = 24_000
TOOL_OVERHEAD_TOKENS_PER_BUILTIN = 400
COMPACT_INACTIVITY_TIMEOUT_S = 90.0
COMPACT_TOTAL_CEILING_S = 600.0
# The Agent gateway drains for 120 s and uvicorn's graceful shutdown allows 120 s inside the container's 150 s stop grace;
# the two drains overlap, so the compaction drain gets the same budget rather than the compaction's own 600 s ceiling.
COMPACT_RUN_DRAIN_TIMEOUT_S = 120.0
