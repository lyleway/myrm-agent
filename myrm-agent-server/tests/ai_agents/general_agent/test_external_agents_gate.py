"""Unit tests for external_agents pure gate helpers (pool/delegate entitlement)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from myrm_agent_harness.toolkits.acp.types import PermissionMode

from app.ai_agents.general_agent.external_agents import (
    BUILTIN_CLI_VISUAL_AGENT_ID,
    ExternalAgentsMixin,
    _auth_mode,
    _cfg_int,
    _config_fingerprint,
    _register_backends_on_pool,
    _resolve_external_agent_cfgs,
    _runtime_pool_scope_id,
    needs_runtime_pool,
    should_mount_invoke_acp_agent_tool,
)
from app.ai_agents.general_agent.external_agents_runtime_config import _permission_mode
from app.services.external_agents.permission_broker import ChatPermissionBroker


def test_auth_mode_resolves_api_key_and_subscription() -> None:
    assert _auth_mode({"authMode": "api_key"}) == "api_key"
    assert _auth_mode({}) == "subscription"


@pytest.mark.parametrize("mode", get_args(PermissionMode))
def test_permission_mode_keeps_each_known_mode(mode: str) -> None:
    assert _permission_mode({"permissionMode": mode}) == mode


@pytest.mark.parametrize("raw", [None, "", "allow-all", "ALLOW_ALL", "ask", "bypass", 3, ["safe"]])
def test_permission_mode_unknown_value_fails_closed_to_safe(raw: object) -> None:
    # A name the harness does not know would stop the agent from launching; ``ask`` and ``bypass`` are names of an
    # older vocabulary that stored settings may still hold.
    assert _permission_mode({"permissionMode": raw}) == "safe"


def test_permission_mode_missing_key_defaults_to_safe() -> None:
    assert _permission_mode({}) == "safe"


def test_register_backends_on_pool_registers_unknown_permission_mode_as_safe() -> None:
    pool = MagicMock()
    _register_backends_on_pool(pool, [{"name": "claude", "command": "claude", "permissionMode": "allow-all"}])
    assert pool.register.call_args[0][1].permission_mode == "safe"


def test_cfg_int_parses_and_falls_back() -> None:
    assert _cfg_int({"maxTurns": 10}, "maxTurns", 25) == 10
    assert _cfg_int({"maxTurns": "12"}, "maxTurns", 25) == 12
    assert _cfg_int({"maxTurns": "bad"}, "maxTurns", 25) == 25
    assert _cfg_int({"maxTurns": True}, "maxTurns", 25) == 25


def test_runtime_pool_scope_id_prefers_scope_then_chat_id() -> None:
    agent = SimpleNamespace(_runtime_pool_scope_id=" scope-1 ", chat_id="chat-2")
    assert _runtime_pool_scope_id(agent) == "scope-1"
    agent2 = SimpleNamespace(_runtime_pool_scope_id=None, chat_id=" chat-2 ")
    assert _runtime_pool_scope_id(agent2) == "chat-2"
    assert _runtime_pool_scope_id(SimpleNamespace()) is None


def test_config_fingerprint_stable_and_skips_disabled() -> None:
    cfgs: list[dict[str, object]] = [
        {"name": "b", "command": "echo", "args": ["-a"], "enabled": True},
        {"name": "a", "command": "claude", "args": [], "enabled": False},
        {"name": "a", "command": "claude", "args": [], "enabled": True},
        {"name": "", "command": "skip", "enabled": True},
        {"enabled": True},
    ]
    fp1 = _config_fingerprint(cfgs)
    fp2 = _config_fingerprint(list(reversed(cfgs)))
    assert fp1 == fp2
    assert len(fp1) == 16


def test_config_fingerprint_changes_when_runtime_fields_change() -> None:
    base_cfg = {
        "name": "claude",
        "type": "cli",
        "command": "/usr/bin/claude",
        "args": ["-p"],
        "authMode": "subscription",
        "env": {"FOO": "bar"},
        "timeout": 300,
        "maxResponseChars": 50000,
        "permissionMode": "allow_all",
        "maxTurns": 25,
        "description": "baseline",
        "enabled": True,
    }
    baseline = _config_fingerprint([base_cfg])

    changed_env = dict(base_cfg)
    changed_env["env"] = {"FOO": "baz"}
    assert _config_fingerprint([changed_env]) != baseline

    changed_timeout = dict(base_cfg)
    changed_timeout["timeout"] = 120
    assert _config_fingerprint([changed_timeout]) != baseline

    changed_max_turns = dict(base_cfg)
    changed_max_turns["maxTurns"] = 12
    assert _config_fingerprint([changed_max_turns]) != baseline

    stored_cwd = dict(base_cfg)
    stored_cwd["cwd"] = "/workspace/subdir"
    # A directory stored with the agent decides nothing: the turn names where it runs.
    assert _config_fingerprint([stored_cwd]) == baseline


@pytest.mark.asyncio
async def test_resolve_external_agent_cfgs_returns_explicit_config() -> None:
    explicit = [{"name": "cli", "command": "echo", "args": []}]
    assert await _resolve_external_agent_cfgs(explicit) == explicit


@pytest.mark.asyncio
async def test_resolve_external_agent_cfgs_non_local_returns_none() -> None:
    with patch("app.config.deploy_mode.is_local_mode", return_value=False):
        assert await _resolve_external_agent_cfgs(None) is None


@pytest.mark.asyncio
async def test_resolve_external_agent_cfgs_auto_detect_local() -> None:
    detected = SimpleNamespace(name="claude", path="/usr/bin/claude")
    seen_include_version: dict[str, bool] = {}

    class _FakeDetector:
        async def detect(self, *, include_version: bool = True) -> list[object]:
            seen_include_version["value"] = include_version
            return [detected]

    with (
        patch("app.config.deploy_mode.is_local_mode", return_value=True),
        patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            _FakeDetector,
        ),
    ):
        cfgs = await _resolve_external_agent_cfgs(None)

    assert cfgs is not None
    assert cfgs[0]["name"] == "claude"
    assert cfgs[0]["command"] == "/usr/bin/claude"
    assert seen_include_version["value"] is False


@pytest.mark.asyncio
async def test_auto_detect_gives_each_agent_the_transport_and_baseline_args_of_its_contract() -> None:
    class _FakeDetector:
        async def detect(self, *, include_version: bool = True) -> list[object]:
            return [
                SimpleNamespace(name="claude", path="/usr/bin/claude"),
                SimpleNamespace(name="gemini", path="/usr/bin/gemini"),
            ]

    with (
        patch("app.config.deploy_mode.is_local_mode", return_value=True),
        patch("myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector", _FakeDetector),
    ):
        cfgs = await _resolve_external_agent_cfgs(None)

    assert cfgs is not None
    by_name = {cfg["name"]: cfg for cfg in cfgs}
    assert by_name["claude"]["type"] == "cli"
    assert by_name["claude"]["args"] == ["-p", "--output-format", "stream-json", "--verbose"]
    # The ACP flags come from the contract when the runtime launches the agent, not from a stored list.
    assert by_name["gemini"]["type"] == "acp"
    assert by_name["gemini"]["args"] == []


@pytest.mark.parametrize(
    ("command", "stored", "expected"),
    [
        # A known agent runs over the transport its contract names, whichever one was stored for it.
        ("/usr/local/bin/gemini", "cli", "acp"),
        ("qwen", "cli", "acp"),
        ("claude", "acp", "cli"),
        # Anything else keeps what was stored: an adapter that merely carries an agent's name is not that agent.
        ("claude-code-acp", "acp", "acp"),
        ("gemini-wrapper", "cli", "cli"),
        ("my-acp-agent", "acp", "acp"),
    ],
)
def test_register_backends_on_pool_runs_a_known_agent_over_its_contract_transport(
    command: str, stored: str, expected: str
) -> None:
    pool = MagicMock()

    _register_backends_on_pool(pool, [{"name": "a", "command": command, "type": stored}])

    assert pool.register.call_args[0][1].backend_type == expected


def test_config_fingerprint_does_not_tell_a_known_agent_apart_by_the_transport_stored_for_it() -> None:
    cli = {"name": "g", "command": "gemini", "type": "cli"}

    assert _config_fingerprint([cli]) == _config_fingerprint([{**cli, "type": "acp"}])
    assert _config_fingerprint([{**cli, "command": "my-agent"}]) != _config_fingerprint(
        [{**cli, "command": "my-agent", "type": "acp"}]
    )


def test_register_backends_on_pool_skips_invalid_and_registers_valid() -> None:
    pool = MagicMock()
    cfgs: list[dict[str, object]] = [
        {"enabled": False, "name": "off", "command": "echo"},
        {"enabled": True, "name": "", "command": "echo"},
        {"enabled": True, "name": "bad", "command": "echo", "type": "invalid"},
        {
            "enabled": True,
            "name": "ok-cli",
            "command": "echo",
            "type": "cli",
            "args": ["-n"],
            "authMode": "api_key",
            "maxTurns": "10",
        },
    ]
    _register_backends_on_pool(pool, cfgs)
    pool.register.assert_called_once()
    assert pool.register.call_args[0][0] == "ok-cli"


@pytest.mark.asyncio
async def test_resolve_external_agent_cfgs_auto_detect_failure_returns_none() -> None:
    with (
        patch("app.config.deploy_mode.is_local_mode", return_value=True),
        patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            side_effect=RuntimeError("detect failed"),
        ),
    ):
        assert await _resolve_external_agent_cfgs(None) is None


@pytest.mark.asyncio
async def test_setup_external_agents_swallows_errors() -> None:
    mixin = ExternalAgentsMixin.__new__(ExternalAgentsMixin)
    mixin.external_agents_config = [{"name": "x", "command": "echo"}]

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("setup failed")

    mixin._do_setup_external_agents = _boom  # type: ignore[method-assign]
    await mixin._setup_external_agents([])


@pytest.mark.asyncio
async def test_ensure_runtime_pool_initializes_when_missing() -> None:
    mixin = ExternalAgentsMixin.__new__(ExternalAgentsMixin)
    mixin._runtime_pool = None
    mixin._do_setup_external_agents = AsyncMock()  # type: ignore[method-assign]

    await mixin._ensure_runtime_pool()
    mixin._do_setup_external_agents.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_runtime_pool_noop_when_pool_exists() -> None:
    mixin = ExternalAgentsMixin.__new__(ExternalAgentsMixin)
    mixin._runtime_pool = object()
    mixin._do_setup_external_agents = AsyncMock()  # type: ignore[method-assign]
    await mixin._ensure_runtime_pool()
    mixin._do_setup_external_agents.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_external_agent_cfgs_auto_detect_empty_returns_none() -> None:
    class _EmptyDetector:
        async def detect(self, *, include_version: bool = True) -> list[object]:
            return []

    with (
        patch("app.config.deploy_mode.is_local_mode", return_value=True),
        patch(
            "myrm_agent_harness.toolkits.acp.core.backend_detector.BackendDetector",
            _EmptyDetector,
        ),
    ):
        assert await _resolve_external_agent_cfgs(None) is None


@pytest.mark.asyncio
async def test_do_setup_returns_early_without_configs() -> None:
    mixin = ExternalAgentsMixin.__new__(ExternalAgentsMixin)
    mixin.external_agents_config = None
    with patch(
        "app.ai_agents.general_agent.external_agents._resolve_external_agent_cfgs",
        new_callable=AsyncMock,
        return_value=None,
    ):
        await mixin._do_setup_external_agents([], mount_invoke_acp_agent_tool=True)
    assert getattr(mixin, "_runtime_pool", None) is None


def test_should_mount_invoke_acp_agent_tool_matrix() -> None:
    assert should_mount_invoke_acp_agent_tool(agent_id="general") is True
    assert should_mount_invoke_acp_agent_tool(agent_id=None) is True
    assert should_mount_invoke_acp_agent_tool(agent_id=BUILTIN_CLI_VISUAL_AGENT_ID) is False


def test_needs_runtime_pool_matrix() -> None:
    assert needs_runtime_pool(enable_external_cli=False, agent_id="builtin-writer") is False
    assert needs_runtime_pool(enable_external_cli=True, agent_id="builtin-developer") is True
    assert needs_runtime_pool(enable_external_cli=False, agent_id=BUILTIN_CLI_VISUAL_AGENT_ID) is True


@pytest.mark.asyncio
async def test_do_setup_ephemeral_pool_without_chat_scope() -> None:
    mixin = ExternalAgentsMixin.__new__(ExternalAgentsMixin)
    mixin.external_agents_config = [{"name": "test-cli", "command": "echo", "args": []}]
    mixin.chat_id = None
    mixin.agent_id = "agent-9"
    mixin._runtime_pool_scope_id = None
    mixin.declared_allowed_roots = ("/workspace/root",)

    mock_pool = MagicMock()
    mock_pool.available_backends = ["test-cli"]
    mock_tool = MagicMock()
    mock_tool.name = "invoke_acp_agent_tool"

    with (
        patch(
            "myrm_agent_harness.toolkits.acp.runtime.pool.RuntimePool",
            return_value=mock_pool,
        ),
        patch(
            "myrm_agent_harness.toolkits.create_invoke_acp_agent_tool",
            return_value=mock_tool,
        ) as mock_create,
    ):
        tools: list[object] = []
        await mixin._do_setup_external_agents(tools, mount_invoke_acp_agent_tool=True)

    assert mixin._owned_runtime_pool is mock_pool
    assert mixin._runtime_pool_from_registry is False
    assert tools == [mock_tool]
    mock_create.assert_called_once_with(
        mock_pool,
        workspace="/workspace/root",
        session_scope=None,
        permissions=ChatPermissionBroker(agent_id="agent-9", chat_id=None, workspace="/workspace/root"),
    )


@pytest.mark.asyncio
async def test_do_setup_passes_chat_scope_to_delegate_tool() -> None:
    mixin = ExternalAgentsMixin.__new__(ExternalAgentsMixin)
    mixin.external_agents_config = [{"name": "test-cli", "command": "echo", "args": []}]
    mixin.chat_id = " chat-scope-1 "
    mixin.agent_id = None
    mixin._runtime_pool_scope_id = None
    mixin.declared_allowed_roots = ("/workspace/chat-scope-1",)

    mock_pool = MagicMock()
    mock_pool.available_backends = ["test-cli"]
    mock_tool = MagicMock()
    mock_tool.name = "invoke_acp_agent_tool"
    mock_registry = MagicMock()
    mock_registry.acquire = AsyncMock(return_value=mock_pool)

    with (
        patch(
            "myrm_agent_harness.toolkits.acp.runtime.pool.RuntimePool",
            return_value=mock_pool,
        ),
        patch(
            "app.services.external_agents.runtime_pool_registry.get_chat_runtime_pool_registry",
            return_value=mock_registry,
        ),
        patch(
            "app.services.external_agents.runtime_pool_registry.ChatScopedRuntimePoolFacade",
            side_effect=lambda pool, *_args: pool,
        ),
        patch(
            "myrm_agent_harness.toolkits.create_invoke_acp_agent_tool",
            return_value=mock_tool,
        ) as mock_create,
    ):
        tools: list[object] = []
        await mixin._do_setup_external_agents(tools, mount_invoke_acp_agent_tool=True)

    assert tools == [mock_tool]
    mock_create.assert_called_once_with(
        mock_pool,
        workspace="/workspace/chat-scope-1",
        session_scope="chat-scope-1",
        # The approvals belong to the chat as stored; only the pool's scope is the trimmed id.
        permissions=ChatPermissionBroker(agent_id="default", chat_id=" chat-scope-1 ", workspace="/workspace/chat-scope-1"),
    )


@pytest.mark.asyncio
async def test_ensure_runtime_pool_swallows_errors() -> None:
    mixin = ExternalAgentsMixin.__new__(ExternalAgentsMixin)
    mixin._runtime_pool = None
    mixin._do_setup_external_agents = AsyncMock(side_effect=RuntimeError("pool init failed"))  # type: ignore[method-assign]

    await mixin._ensure_runtime_pool()
    mixin._do_setup_external_agents.assert_awaited_once()
