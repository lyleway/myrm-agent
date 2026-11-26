"""The security gate of a turn forced onto an external agent reads what a request brings, as the model's call does."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from app.ai_agents.general_agent.stream_pipeline import execute_stream_pipeline
from app.core.security.session_overlay import SecurityOverlaySpec
from tests.support.forced_delegate_agent import DELEGATED, forced_delegate_agent, saved_security


def _permissions(**actions: str) -> dict[str, object]:
    return {"permissions": dict(actions)}


async def _run(agent: SimpleNamespace) -> list[dict[str, object]]:
    return [event async for event in execute_stream_pipeline(agent, "hi", force_external_agent="echo-cli")]


def _error_types(events: list[dict[str, object]]) -> list[str]:
    return [str(event["diagnostic_result"]["error_type"]) for event in events if event["type"] == "error"]  # type: ignore[index]


async def test_a_user_who_never_saved_a_security_policy_can_address_an_agent() -> None:
    agent = forced_delegate_agent(backends=["echo-cli"])

    with saved_security(None):
        assert await _run(agent) == [DELEGATED]


async def test_an_ask_is_the_users_own_confirmation_and_goes_through() -> None:
    agent = forced_delegate_agent(backends=["echo-cli"])

    with saved_security(_permissions(invoke_external_agent="ask")):
        assert await _run(agent) == [DELEGATED]


@pytest.mark.parametrize("surface_action", ["ask", "allow"])
async def test_the_remote_tools_setting_short_of_a_deny_lets_the_turn_through(surface_action: str) -> None:
    agent = forced_delegate_agent(backends=["echo-cli"])

    with saved_security(_permissions(remote_tools=surface_action)):
        assert await _run(agent) == [DELEGATED]


async def test_a_saved_deny_stops_the_turn_before_any_pool_is_built() -> None:
    agent = forced_delegate_agent(backends=["echo-cli"])

    with saved_security(_permissions(invoke_external_agent="deny")):
        events = await _run(agent)

    assert _error_types(events) == ["external_agent_denied"]
    assert agent.delegated == [] and agent.pool_requests == 0


async def test_the_high_security_remote_tools_setting_stops_the_turn() -> None:
    agent = forced_delegate_agent(backends=["echo-cli"])

    with saved_security(_permissions(remote_tools="deny")):
        events = await _run(agent)

    assert _error_types(events) == ["external_agent_denied"]
    assert agent.delegated == []


async def test_the_agents_own_deny_stops_a_turn_the_saved_policy_allows() -> None:
    agent = forced_delegate_agent(backends=["echo-cli"], agent_security=_permissions(invoke_external_agent="deny"))

    with saved_security(_permissions(invoke_external_agent="allow")):
        events = await _run(agent)

    assert _error_types(events) == ["external_agent_denied"]
    assert agent.delegated == []


async def test_a_read_only_chat_is_told_to_switch_its_preset_and_not_to_open_the_settings() -> None:
    agent = forced_delegate_agent(backends=["echo-cli"], overlay=SecurityOverlaySpec(preset="explore"))

    with saved_security(_permissions(invoke_external_agent="allow")):
        events = await _run(agent)

    assert _error_types(events) == ["external_agent_denied_session_preset"]
    assert "recovery_actions" not in events[0]
    assert agent.delegated == []


async def test_a_remote_connection_is_told_to_use_the_agents_own_computer_and_not_to_open_the_settings() -> None:
    overlay = SecurityOverlaySpec(trust_zone="remote_exposed", admission_path="tunnel")
    agent = forced_delegate_agent(backends=["echo-cli"], overlay=overlay)

    with saved_security(_permissions(invoke_external_agent="allow")):
        events = await _run(agent)

    assert _error_types(events) == ["external_agent_denied_remote"]
    assert "recovery_actions" not in events[0]
    assert agent.delegated == []


async def test_an_auto_approve_edits_chat_goes_through_where_the_model_would_have_to_ask() -> None:
    agent = forced_delegate_agent(backends=["echo-cli"], overlay=SecurityOverlaySpec(preset="accept_edits"))

    with saved_security(_permissions(invoke_external_agent="allow")):
        assert await _run(agent) == [DELEGATED]


async def test_a_policy_changed_between_two_messages_applies_to_the_second_one() -> None:
    agent = forced_delegate_agent(backends=["echo-cli"])

    with saved_security(_permissions(invoke_external_agent="allow")):
        first = await _run(agent)
    with saved_security(_permissions(invoke_external_agent="deny")):
        second = await _run(agent)

    assert first == [DELEGATED]
    assert _error_types(second) == ["external_agent_denied"]


async def test_the_agents_overrides_are_read_again_for_every_message(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = SimpleNamespace(security_overrides=_permissions(invoke_external_agent="deny"))

    class _Resolver:
        async def resolve(self, agent_id: str) -> SimpleNamespace:
            return resolved

    monkeypatch.setattr("app.services.agent.profile.profile_resolver.get_agent_profile_resolver", _Resolver)
    agent = forced_delegate_agent(backends=["echo-cli"])
    agent.agent_id = "reviewer"  # built before the agent's overrides were tightened: it carries none

    with saved_security(_permissions(invoke_external_agent="allow")):
        events = await _run(agent)

    assert _error_types(events) == ["external_agent_denied"]
    assert agent.delegated == []


async def test_the_refusal_logs_its_cause_and_the_policy_reason_and_shows_neither(caplog: pytest.LogCaptureFixture) -> None:
    with (
        saved_security(_permissions(invoke_external_agent="deny")),
        caplog.at_level(logging.WARNING, logger="app.ai_agents.general_agent.forced_delegate"),
    ):
        events = await _run(forced_delegate_agent(backends=["echo-cli"]))

    shown = json.dumps(events)
    assert "invoke_acp_agent_tool" not in shown and "denied by" not in shown.lower()
    assert "blocked by security policy" in caplog.text and "cause=policy" in caplog.text
    assert "invoke_acp_agent_tool" in caplog.text
