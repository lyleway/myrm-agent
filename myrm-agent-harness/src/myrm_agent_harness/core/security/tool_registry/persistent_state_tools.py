"""Built-in tools that change what outlives the turn: long-term memory, the kanban board, installed skills.

[INPUT]
- (none — pure data)

[OUTPUT]
- PERSISTENT_STATE_MUTATOR_TOOLS: the built-in tools, each governed under its own name, whose calls write a memory
  entry, plan or cancel work on the kanban board, or install a skill

[POS]
The one list of the persistent-state mutators that are neither file nor shell tools. A read-only session denies them
and the untrusted ingress fence fences them, both from this list, so the two postures cannot drift apart. The kanban
worker tools (complete, block, comment, attach, heartbeat, show) are not here: a worker reports on the one task it
is bound to and must keep working under any posture.
"""

from __future__ import annotations

PERSISTENT_STATE_MUTATOR_TOOLS: frozenset[str] = frozenset(
    {
        # A saved or edited memory entry steers every later turn that reads its namespace.
        "memory_save_tool",
        "memory_manage_tool",
        # The orchestrator side of the kanban board: planning, unblocking, cancelling and retrying schedule agent work.
        "kanban_add_task",
        "kanban_unblock",
        "kanban_cancel_task",
        "kanban_retry_task",
        "kanban_revise_plan",
        # An installed skill is code the agent runs on later turns.
        "skill_market_tool",
    }
)
