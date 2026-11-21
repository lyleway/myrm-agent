"""Transcript classifier types: the reviewer protocol of auto mode and its decision, result and call records.

[INPUT]
- (none — pure types, zero dependencies)

[OUTPUT]
- ReviewDecision / ReviewResult / RecentToolCall / SecurityReviewerProtocol: Layer 5.5 Transcript Classifier

[POS]
The contract an LLM-based reviewer implements to decide the asks the deterministic engine leaves open. Imports nothing
from the security subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ReviewDecision(StrEnum):
    """LLM security reviewer decision for a shell command."""

    ALLOW = "allow"
    DENY = "deny"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Result from a security reviewer evaluation."""

    decision: ReviewDecision
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RecentToolCall:
    """Compact representation of a recent tool call for transcript context.

    Only tool_name and args are included (Reasoning-Blind: no assistant
    text, no tool output, no descriptions).
    """

    tool_name: str
    args: dict[str, object]


@runtime_checkable
class SecurityReviewerProtocol(Protocol):
    """6th framework protocol — LLM-based Transcript Classifier for auto-mode.

    Invoked when the deterministic security engine cannot classify a tool call
    (returns ASK) and ``auto_mode_enabled`` is True. The classifier examines
    user intent + recent tool call sequence (Reasoning-Blind) to decide
    whether the action is safe to auto-execute.

    Implementations must be fail-safe: any exception or timeout should be
    treated as UNCERTAIN, causing fallback to HITL approval.
    """

    async def review(
        self,
        command: str,
        *,
        workspace_root: str | None = None,
        intent_context: str | None = None,
        taint_labels: frozenset[str] | None = None,
        recent_tool_calls: tuple[RecentToolCall, ...] = (),
        model_id: str | None = None,
        trusted_domains: tuple[str, ...] = (),
    ) -> ReviewResult:
        """Evaluate whether a tool call is safe to auto-execute.

        Args:
            command: String representation of the tool call to evaluate.
            workspace_root: Current working directory context.
            intent_context: Recent human messages representing the user's intent
                (Reasoning-Blind: only user messages, no assistant text).
            taint_labels: Taint labels currently active in the session.
            recent_tool_calls: Recent tool call sequence for cross-tool context.
            model_id: Optional ID of the specific LLM model to use.
            trusted_domains: Domains the user has explicitly marked as trusted
                (from network_allowlist). Helps distinguish internal vs external.

        Returns:
            ReviewResult with ALLOW, DENY, or UNCERTAIN decision.
        """
        ...
