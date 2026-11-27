"""What a delegated turn reports: its usage and counts, the notice steps the UI words itself and the note the model gets.

[INPUT]
- myrm_agent_harness.core.events.types::AgentEventType (POS: streaming event types and enums, framework-agnostic)
- myrm_agent_harness.toolkits.acp.types::StopReason (POS: ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, serving as the foundation for the entire ACP module)

[OUTPUT]
- DelegateUsage: Token usage statistics from an external agent delegation.
- DelegateMeta: Metadata collected during an external agent delegation turn.
- STOP_NOTES: what the model is told when an agent ended its turn before it finished
- notice_step: a finished, title-only step the UI words itself
- as_int: best-effort int coercion of a loosely typed event payload

[POS]
Leaf helpers of ``acp_agent_tools``: the shapes a model-initiated delegation hands back, kept apart from the loop that
collects them.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from myrm_agent_harness.core.events.types import AgentEventType
from myrm_agent_harness.toolkits.acp.types import StopReason


class DelegateUsage(TypedDict):
    """Token usage statistics from an external agent delegation."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


class DelegateMeta(TypedDict):
    """Metadata collected during an external agent delegation turn."""

    tool_calls: int
    errors: int
    usage: NotRequired[DelegateUsage]


# What the model is told when an agent ended its turn before it finished; the model decides whether to carry on.
STOP_NOTES = {
    StopReason.MAX_TOKENS: "[stopped — the agent reached its output limit; the reply above is incomplete]",
    StopReason.MAX_TURN_REQUESTS: "[stopped — the agent reached its own turn limit]",
    StopReason.REFUSAL: "[stopped — the agent declined to continue]",
}


def notice_step(agent_name: str, kind: str) -> dict[str, object]:
    """A finished step the UI words itself (title only), not a step that is still running."""
    return {
        "type": AgentEventType.TASKS_STEPS.value,
        "step_key": f"delegation_{agent_name}_{kind}",
        "tool_name": f"delegate:{agent_name}",
        "status": "completed",
        "data": [],
    }


def as_int(value: object) -> int:
    """Best-effort int coercion for loosely-typed (``object``) event payloads."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return 0
