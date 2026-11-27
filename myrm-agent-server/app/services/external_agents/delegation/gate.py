"""The security gate of a turn the user addresses to one external agent.

[INPUT]
- myrm_agent_harness.agent.security.engine::evaluate_tool_call (POS: the permission decision of one tool call)
- myrm_agent_harness.agent.security.tool_registry::resolve_permission_type (POS: the permission a tool call is judged as)
- myrm_agent_harness.agent.security.trust_context::RequestTrustContext (POS: whether a request arrived over a remote connection)
- app.core.security.session_overlay::SecurityOverlaySpec (POS: the overlays a request lays over the saved security config)

[OUTPUT]
- DenialCause: what stands between the user and the agent (remote connection, the chat's security preset, the policy)
- Denial: a refused turn's cause and the policy engine's wording
- direct_turn_denial: why the policy does not let the user's own turn go to the agent, or None when it may

[POS]
The user's own turn is judged as the call the model would make: the same tool, the same effective config (channel
preset, saved policy, the agent's overrides and the request's overlays), the same engine. Only a deny stops it: an
ask is the approval the model-initiated call has to collect, and the user gave it by naming the agent. The cause is
attributed by lifting one thing at a time and judging again, so the guidance never sends the user to change
something that would not let the turn through.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from myrm_agent_harness.agent.security.engine import evaluate_tool_call
from myrm_agent_harness.agent.security.tool_registry import resolve_permission_type
from myrm_agent_harness.agent.security.trust_context import RequestTrustContext
from myrm_agent_harness.agent.security.types import PermissionAction

if TYPE_CHECKING:
    from collections.abc import Callable

    from myrm_agent_harness.agent.security.types import SecurityConfig

    from app.core.security.session_overlay import SecurityOverlaySpec

# The tool a model-initiated delegation goes through; the user's own turn is judged as a call of it.
_DELEGATE_TOOL: Final = "invoke_acp_agent_tool"


class DenialCause(StrEnum):
    """What stands between the user and the agent."""

    # The request arrived over a remote connection: nothing in this chat or in Settings lifts it from there.
    REMOTE = "remote"
    # The chat's security preset (read-only): switching the preset lifts it.
    SESSION_PRESET = "session_preset"
    # The saved policy, the agent's own rules or the channel: Settings is where they change.
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class Denial:
    """A refused turn: ``reason`` is the policy engine's own wording, which belongs in the log and not before the user."""

    cause: DenialCause
    reason: str


def _verdict(agent: str, config: SecurityConfig) -> tuple[PermissionAction, str]:
    arguments: dict[str, object] = {"agent_name": agent}
    return evaluate_tool_call(resolve_permission_type(_DELEGATE_TOOL, arguments), arguments, config, tool_name=_DELEGATE_TOOL)


def direct_turn_denial(
    agent: str, overlay: SecurityOverlaySpec, config_under: Callable[[SecurityOverlaySpec], SecurityConfig]
) -> Denial | None:
    """Why the policy does not let the user's own turn go to ``agent`` (None: it may).

    ``config_under`` builds the effective security config of the chat turn for a given set of request overlays:
    ``overlay`` is the request's own, and the attribution asks the same question again with one overlay lifted.
    """
    action, reason = _verdict(agent, config_under(overlay))
    if action is not PermissionAction.DENY:
        return None
    remote = RequestTrustContext.from_admission(
        trust_zone=overlay.trust_zone, admission_path=overlay.admission_path
    ).restrict_destructive_tools
    if remote:
        return Denial(DenialCause.REMOTE, reason)
    if overlay.preset is not None:
        without_preset = overlay.model_copy(update={"preset": None})
        if _verdict(agent, config_under(without_preset))[0] is not PermissionAction.DENY:
            return Denial(DenialCause.SESSION_PRESET, reason)
    return Denial(DenialCause.POLICY, reason)
