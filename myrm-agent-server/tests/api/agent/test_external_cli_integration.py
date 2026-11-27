"""Integration: external_cli entitlement via agent-stream tools_snapshot."""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from tests.api.agent.test_capability_gap_integration import _collect_agent_stream
from tests.api.agent.utils import check_e2e_errors, get_lite_model_selection

if TYPE_CHECKING:
    from app.core.channel_bridge.config_loader import UserConfigs


def _user_configs(security_config: dict[str, object] | None) -> UserConfigs:
    """The canned user configs whose saved securityConfig is *security_config*."""
    from app.core.channel_bridge.config_loader import UserConfigs
    from tests.api.agent.conftest import _build_mock_user_configs

    configs = _build_mock_user_configs()
    assert isinstance(configs, UserConfigs)
    configs.security_config_dict = security_config
    return configs


def _tools_snapshot_event(events: list[dict[str, object]]) -> dict[str, object] | None:
    snapshot = next(
        (event for event in events if event.get("type") == "tools_snapshot"),
        None,
    )
    return snapshot if isinstance(snapshot, dict) else None


def _tool_names_from_snapshot(events: list[dict[str, object]]) -> set[str]:
    tools_snapshot = _tools_snapshot_event(events)
    if tools_snapshot is None:
        return set()
    snapshot_rows = tools_snapshot.get("data")
    if not isinstance(snapshot_rows, list):
        return set()
    return {str(row.get("name")) for row in snapshot_rows if isinstance(row, dict) and row.get("name")}


def _enabled_builtin_from_snapshot(events: list[dict[str, object]]) -> list[str] | None:
    tools_snapshot = _tools_snapshot_event(events)
    if tools_snapshot is None:
        return None
    snapshot_data = tools_snapshot.get("data")
    if not isinstance(snapshot_data, dict):
        return None
    enabled = snapshot_data.get("enabled_builtin_tools")
    return list(enabled) if isinstance(enabled, list) else None


def _external_cli_payload(chat_id: str, *, include_external_cli: bool) -> dict[str, object]:
    enabled = ["web_search", "memory"]
    if include_external_cli:
        enabled.append("external_cli")
    return {
        "query": "Reply with the word OK only.",
        "message_id": f"test-ext-cli-{chat_id}",
        "chat_id": chat_id,
        "action_mode": "agent",
        "model_selection": get_lite_model_selection(),
        "agent_config": {
            "enabled_builtin_tools": enabled,
            "skill_ids": [],
        },
        "timezone": "UTC",
    }


@pytest.mark.e2e
def test_agent_stream_external_cli_off_excludes_delegate_tool(
    client: TestClient,
) -> None:
    """Writer-style profile without external_cli must not mount invoke_acp_agent_tool Turn1."""
    chat_id = f"test_ext_cli_off_{uuid.uuid4().hex[:8]}"
    events = _collect_agent_stream(client, _external_cli_payload(chat_id, include_external_cli=False))
    check_e2e_errors(events)

    tool_names = _tool_names_from_snapshot(events)
    if tool_names:
        assert "invoke_acp_agent_tool" not in tool_names


@pytest.mark.e2e
@patch(
    "app.ai_agents.general_agent.external_agents._resolve_external_agent_cfgs",
    new=AsyncMock(return_value=[{"name": "echo-cli", "type": "cli", "command": "echo", "args": []}]),
)
def test_agent_stream_external_cli_on_mounts_delegate_when_backends_exist(
    client: TestClient,
) -> None:
    """external_cli ON + resolvable CLI backends must expose invoke_acp_agent_tool Turn1."""
    chat_id = f"test_ext_cli_on_{uuid.uuid4().hex[:8]}"
    events = _collect_agent_stream(client, _external_cli_payload(chat_id, include_external_cli=True))
    check_e2e_errors(events)

    tool_names = _tool_names_from_snapshot(events)
    if tool_names:
        assert "invoke_acp_agent_tool" in tool_names


@pytest.mark.e2e
def test_explore_security_preset_denies_invoke_external_in_merged_config() -> None:
    """Lane-C regression: explore preset must deny invoke_external_agent (readonly-class bug)."""
    from myrm_agent_harness.agent.security.types import PermissionAction

    from app.core.security.session_overlay import apply_session_preset
    from tests.support.overlay_verdict import verdict_under

    base: dict[str, object] = {
        "permissions": {
            "spawn_subagent": "allow",
            "invoke_external_agent": "allow",
        }
    }
    merged = apply_session_preset(base, "explore")
    assert verdict_under(merged, "delegate_task_tool", {"mode": "x"}) is PermissionAction.ALLOW
    assert verdict_under(merged, "invoke_acp_agent_tool", {"agent_name": "claude"}) is PermissionAction.DENY


def _delegate_stream_started(events: list[dict[str, object]]) -> bool:
    for event in events:
        try:
            raw = json.dumps(event)
        except TypeError:
            continue
        if "delegate:" in raw or "delegation_" in raw:
            return True
    return False


def _error_types(events: list[dict[str, object]]) -> list[str]:
    """The ``diagnostic_result.error_type`` of every error event: the stable code a refusal travels under."""
    types: list[str] = []
    for event in events:
        diagnostic = event.get("diagnostic_result")
        if event.get("type") == "error" and isinstance(diagnostic, dict):
            types.append(str(diagnostic.get("error_type")))
    return types


def _policy_block_log(caplog: pytest.LogCaptureFixture) -> str:
    """The policy engine's own wording, which stays in the log instead of the user-facing error."""
    return " ".join(
        record.getMessage().lower()
        for record in caplog.records
        if record.name == "app.ai_agents.general_agent.forced_delegate" and record.levelno == logging.WARNING
    )


@pytest.mark.e2e
def test_readonly_security_config_spawn_allow_invoke_deny_engine() -> None:
    """DPSEAG: readonly profile allows internal spawn but denies external CLI."""
    from myrm_agent_harness.agent.security.engine import evaluate_tool_call
    from myrm_agent_harness.agent.security.types import PermissionAction, SecurityConfig

    config = SecurityConfig.readonly()
    spawn_action, _ = evaluate_tool_call("spawn_subagent", {}, config)
    external_action, _ = evaluate_tool_call(
        "invoke_external_agent",
        {"agent": "echo-cli"},
        config,
    )
    assert spawn_action == PermissionAction.ALLOW
    assert external_action == PermissionAction.DENY


_ECHO_CLI_BACKEND = patch(
    "app.ai_agents.general_agent.external_agents._resolve_external_agent_cfgs",
    new=AsyncMock(return_value=[{"name": "echo-cli", "type": "cli", "command": "echo", "args": []}]),
)


def _force_external_payload(chat_id: str, **overrides: object) -> dict[str, object]:
    """A chat request in which the user addresses the external agent ``echo-cli`` directly."""
    return {
        "query": "Run external CLI task",
        "message_id": f"msg-{chat_id}",
        "chat_id": chat_id,
        "action_mode": "agent",
        "force_external_agent": "echo-cli",
        "model_selection": get_lite_model_selection(),
        "agent_config": {
            "enabled_builtin_tools": ["external_cli"],
            "skill_ids": [],
        },
        "timezone": "UTC",
        **overrides,
    }


def _saved_security(**permissions: str) -> dict[str, object]:
    """A securityConfig the user saved: the given permissions, everything else as the defaults leave it."""
    return {
        "capabilities": [{"permission": "*", "pattern": "*"}],
        "permissions": {"spawn_subagent": "allow", "file_write": "ask", "shell_exec": "ask", **permissions},
        "yoloModeEnabled": False,
        "autoModeEnabled": False,
    }


def _the_agent_answered(events: list[dict[str, object]]) -> bool:
    """Whether the external agent's own reply reached the stream: ``echo`` hands the user's message back."""
    return any(event.get("type") == "message" and "Run external CLI task" in str(event.get("data")) for event in events)


@pytest.mark.e2e
@_ECHO_CLI_BACKEND
def test_an_ask_is_the_users_own_confirmation_for_force_external(
    client: TestClient,
    mock_load_user_configs: AsyncMock,
) -> None:
    """The user named the agent: an ``invoke_external_agent`` ask is their confirmation, and the agent answers."""
    mock_load_user_configs.return_value = _user_configs(_saved_security(invoke_external_agent="ask"))

    events = _collect_agent_stream(client, _force_external_payload(f"test_ask_ext_{uuid.uuid4().hex[:8]}"))

    assert _error_types(events) == [], events
    assert _the_agent_answered(events), events


@pytest.mark.e2e
@_ECHO_CLI_BACKEND
def test_workspace_security_config_blocks_force_external_on_deny(
    client: TestClient,
    mock_load_user_configs: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Lane-C: a saved ``invoke_external_agent`` deny blocks direct force_external routing."""
    mock_load_user_configs.return_value = _user_configs(_saved_security(invoke_external_agent="deny"))

    events = _collect_agent_stream(client, _force_external_payload(f"test_workspace_ext_block_{uuid.uuid4().hex[:8]}"))

    assert _error_types(events) == ["external_agent_denied"], events
    blocked = _policy_block_log(caplog)
    assert "blocked by security policy" in blocked and "denied" in blocked
    assert not _delegate_stream_started(events), events


@pytest.mark.e2e
@_ECHO_CLI_BACKEND
def test_the_high_security_remote_tools_setting_blocks_force_external(
    client: TestClient,
    mock_load_user_configs: AsyncMock,
) -> None:
    """The high-security preset writes ``remote_tools: deny``; delegating to an external agent is what it governs."""
    mock_load_user_configs.return_value = _user_configs(_saved_security(remote_tools="deny"))

    events = _collect_agent_stream(client, _force_external_payload(f"test_remote_tools_ext_{uuid.uuid4().hex[:8]}"))

    assert _error_types(events) == ["external_agent_denied"], events
    assert not _delegate_stream_started(events), events


@pytest.mark.e2e
@_ECHO_CLI_BACKEND
def test_readonly_security_config_blocks_force_external_delegate_stream(
    client: TestClient,
    mock_load_user_configs: AsyncMock,
) -> None:
    """Lane-C: builtin readonly profile must block force_external_agent direct routing."""
    mock_load_user_configs.return_value = _user_configs(
        _saved_security(invoke_external_agent="deny", file_write="deny", shell_exec="deny")
    )

    events = _collect_agent_stream(client, _force_external_payload(f"test_readonly_ext_block_{uuid.uuid4().hex[:8]}"))

    assert _error_types(events) == ["external_agent_denied"], events
    assert not _delegate_stream_started(events), events


@pytest.mark.e2e
@_ECHO_CLI_BACKEND
def test_explore_preset_blocks_force_external_delegate_stream(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Lane-C: the explore preset denies invoke_external_agent; the user is told to switch the preset."""
    payload = _force_external_payload(f"test_explore_ext_block_{uuid.uuid4().hex[:8]}", security_preset="explore")

    events = _collect_agent_stream(client, payload)

    assert _error_types(events) == ["external_agent_denied_session_preset"], events
    blocked = _policy_block_log(caplog)
    assert "cause=session_preset" in blocked and "invoke_acp_agent_tool" in blocked
    assert not _delegate_stream_started(events), events


@pytest.mark.e2e
@_ECHO_CLI_BACKEND
def test_auto_approve_edits_preset_lets_force_external_through(client: TestClient) -> None:
    """The accept_edits preset asks about external agents; the user addressing one is that approval."""
    payload = _force_external_payload(f"test_accept_edits_ext_{uuid.uuid4().hex[:8]}", security_preset="accept_edits")

    events = _collect_agent_stream(client, payload)

    assert _error_types(events) == [], events
    assert _the_agent_answered(events), events


@pytest.mark.e2e
@_ECHO_CLI_BACKEND
def test_a_user_who_never_saved_a_security_policy_can_force_external(
    client: TestClient,
    mock_load_user_configs: AsyncMock,
) -> None:
    """Nothing saved is the defaults, which the model's own delegation meets too; it is no reason to refuse."""
    mock_load_user_configs.return_value = _user_configs(None)

    events = _collect_agent_stream(client, _force_external_payload(f"test_missing_sec_ext_{uuid.uuid4().hex[:8]}"))

    assert _error_types(events) == [], events
    assert _the_agent_answered(events), events


@pytest.mark.e2e
def test_agent_stream_external_cli_on_skips_delegate_without_backends(
    client: TestClient,
) -> None:
    """external_cli ON alone must not mount delegate when no CLI backends resolve."""
    chat_id = f"test_ext_cli_no_backend_{uuid.uuid4().hex[:8]}"
    with patch(
        "app.ai_agents.general_agent.external_agents._resolve_external_agent_cfgs",
        new=AsyncMock(return_value=None),
    ):
        events = _collect_agent_stream(client, _external_cli_payload(chat_id, include_external_cli=True))
    check_e2e_errors(events)

    tool_names = _tool_names_from_snapshot(events)
    if tool_names:
        assert "invoke_acp_agent_tool" not in tool_names
