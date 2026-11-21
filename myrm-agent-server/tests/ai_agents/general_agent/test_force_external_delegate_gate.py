"""Unit tests for the security gate of a turn forced onto an external agent."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from app.ai_agents.general_agent.forced_delegate import denial_reason
from app.core.security.session_overlay import SecurityOverlaySpec

if TYPE_CHECKING:
    from app.ai_agents.general_agent.agent import GeneralAgent


def _agent(security_config_raw: dict[str, object] | None) -> GeneralAgent:
    """A stand-in that carries only the field the gate reads."""
    return cast("GeneralAgent", SimpleNamespace(security_config_raw=security_config_raw))


def _security_raw(*, invoke_external: str) -> dict[str, object]:
    return {
        "capabilities": [{"permission": "*", "pattern": "*"}],
        "permissions": {
            "spawn_subagent": "allow",
            "invoke_external_agent": invoke_external,
            "file_write": "ask",
            "shell_exec": "ask",
        },
        "yoloModeEnabled": False,
        "autoModeEnabled": False,
    }


def test_force_external_denied_when_security_config_missing() -> None:
    agent = _agent(None)
    reason = denial_reason(agent, "echo-cli")
    assert reason is not None
    assert "security config missing" in reason.lower()


def test_force_external_allowed_when_invoke_external_allowed() -> None:
    agent = _agent(_security_raw(invoke_external="allow"))
    reason = denial_reason(agent, "echo-cli")
    assert reason is None


def test_force_external_denied_when_invoke_external_ask() -> None:
    agent = _agent(_security_raw(invoke_external="ask"))
    reason = denial_reason(agent, "echo-cli")
    assert reason is not None
    assert "ask" in reason.lower()


@pytest.mark.parametrize(
    "overlay",
    [
        SecurityOverlaySpec(preset="explore"),
        SecurityOverlaySpec(preset="accept_edits"),
        SecurityOverlaySpec(trust_zone="remote_exposed", admission_path="tunnel"),
    ],
    ids=["explore", "accept_edits", "remote_exposed"],
)
def test_force_external_is_held_back_by_a_request_overlay_over_a_saved_allow(overlay: SecurityOverlaySpec) -> None:
    agent = _agent(overlay.apply(_security_raw(invoke_external="allow")))
    assert denial_reason(agent, "echo-cli") is not None
