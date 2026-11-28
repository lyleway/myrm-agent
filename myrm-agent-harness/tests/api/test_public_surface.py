"""Smoke tests for ``myrm_agent_harness.api`` — the external integration surface."""

from __future__ import annotations

import ast
import importlib
import inspect
import subprocess
import sys
from pathlib import Path
from typing import Protocol

import pytest


@pytest.mark.api
def test_api_package_exports_match_all() -> None:
    api = importlib.import_module("myrm_agent_harness.api")
    export_keys = sorted(api._EXPORTS.keys())
    assert sorted(api.__all__) == export_keys


@pytest.mark.api
def test_api_stub_mirrors_lazy_registry() -> None:
    """The ``.pyi`` stub is what gives type checkers the real type of each lazy export."""
    api = importlib.import_module("myrm_agent_harness.api")
    assert api.__file__ is not None
    stub = ast.parse(Path(api.__file__).with_suffix(".pyi").read_text(encoding="utf-8"))
    imports = [(node.module, alias) for node in stub.body if isinstance(node, ast.ImportFrom) for alias in node.names]

    # ``X as X`` is the only spelling a stub uses to re-export a name.
    assert [alias.name for _, alias in imports if alias.asname != alias.name] == []
    assert {alias.name: (module, alias.name) for module, alias in imports} == api._EXPORTS


@pytest.mark.api
def test_api_lazy_exports_resolve_and_cache() -> None:
    import myrm_agent_harness.api as api

    for name in api.__all__:
        api.__dict__.pop(name, None)

    for name in api.__all__:
        value = getattr(api, name)
        assert value is not None
        assert api.__dict__[name] is value


@pytest.mark.api
def test_api_dir_lists_every_export_and_an_unknown_name_is_an_attribute_error() -> None:
    api = importlib.import_module("myrm_agent_harness.api")

    # ``dir()`` sorts whatever ``__dir__`` returns, so the module hook is read directly for what completers see.
    assert api.__dir__() == sorted(api.__all__)
    assert dir(api) == sorted(api.__all__)
    with pytest.raises(AttributeError, match="has no attribute 'no_such_export'"):
        _ = api.no_such_export


@pytest.mark.api
def test_api_config_submodule_exports() -> None:
    from myrm_agent_harness.api.config import (
        AgentConfig,
        ConfigIncompleteError,
        LLMConfig,
        ToolGatewayConfig,
    )

    assert inspect.isclass(AgentConfig)
    assert inspect.isclass(LLMConfig)
    assert inspect.isclass(ToolGatewayConfig)
    assert issubclass(ConfigIncompleteError, Exception)


@pytest.mark.api
def test_api_types_submodule_exports_without_factory() -> None:
    before = set(sys.modules)
    types_mod = importlib.import_module("myrm_agent_harness.api.types")
    loaded = set(sys.modules) - before

    assert "myrm_agent_harness.agent.skill_agent.factory" not in loaded
    assert hasattr(types_mod, "AgentRuntimeConfig")
    assert hasattr(types_mod, "AgentStreamEvent")
    assert hasattr(types_mod, "CompletionStatus")


# What a host that embeds the harness gets for running turns on an agent it does not own. Every name here is a promise
# of compatibility, so the list is spelled out and a name is added only on purpose; the login and credential state of
# the vendors' CLIs, installing a CLI and the inbound ACP server are deliberately not in it.
_DELEGATION_SURFACE = frozenset(
    {
        "AcpError",
        "AcpErrorCode",
        "AuthMode",
        "BackendType",
        "CliSessionRecord",
        "CliSessionStore",
        "ClientIdentity",
        "ContinuityStatus",
        "HttpMcpServer",
        "McpServerConfig",
        "PermissionBroker",
        "PermissionMode",
        "PermissionRequest",
        "PermissionVerdict",
        "RuntimeConfig",
        "RuntimeEvent",
        "RuntimeEventType",
        "RuntimePool",
        "StdioMcpServer",
        "StopReason",
        "create_invoke_acp_agent_tool",
    }
)

# The smallest CLI a host can delegate to: one event line carrying the prompt it was given as its last argument.
_FAKE_CLI = (
    'import json, sys\nprint(json.dumps({"type": "assistant", "content": "[cli] " + sys.argv[-1]}), flush=True)\n'
)


@pytest.mark.api
def test_api_delegation_submodule_is_exactly_the_committed_surface() -> None:
    delegation = importlib.import_module("myrm_agent_harness.api.delegation")

    assert set(delegation.__all__) == _DELEGATION_SURFACE
    assert delegation.__all__ == sorted(delegation.__all__)
    for name in delegation.__all__:
        assert getattr(delegation, name) is not None


@pytest.mark.api
def test_api_delegation_imports_without_the_acp_extra() -> None:
    """A host that installed the harness alone imports the surface; only a turn on an ACP agent needs the extra."""
    code = "import sys; sys.modules['acp'] = None; import myrm_agent_harness.api.delegation"

    imported = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False)

    assert imported.returncode == 0, imported.stderr


@pytest.mark.api
async def test_api_delegation_runs_a_turn_with_nothing_but_its_own_names(tmp_path: Path) -> None:
    """The example a host copies: register a CLI, run a turn, read the events."""
    from myrm_agent_harness.api.delegation import RuntimeConfig, RuntimeEventType, RuntimePool

    script = tmp_path / "fakecli.py"
    script.write_text(_FAKE_CLI, encoding="utf-8")
    pool = RuntimePool(max_concurrent=1)
    pool.register(
        "fake", RuntimeConfig(backend_type="cli", command=sys.executable, args=[str(script)], timeout_seconds=60)
    )
    try:
        events = [event async for event in pool.run_turn("fake", "ping", session_id="chat-1", workspace=str(tmp_path))]
    finally:
        await pool.close_all()

    assert "".join(str(e.data["content"]) for e in events if e.type is RuntimeEventType.TEXT_DELTA) == "[cli] ping"
    assert events[-1].type is RuntimeEventType.DONE


@pytest.mark.api
def test_api_protocols_are_extension_contracts() -> None:
    from myrm_agent_harness.api.protocols import (
        AgentProfileBackend,
        CronStore,
        HookRegistryProtocol,
        IntegrationProvider,
        KanbanStore,
        MerchantBackend,
        SkillBackend,
        StorefrontBackend,
    )

    protocol_types = (
        AgentProfileBackend,
        CronStore,
        HookRegistryProtocol,
        IntegrationProvider,
        KanbanStore,
        MerchantBackend,
        SkillBackend,
        StorefrontBackend,
    )
    for protocol_type in protocol_types:
        assert inspect.isclass(protocol_type)
        assert issubclass(protocol_type, Protocol) or getattr(protocol_type, "_is_protocol", False)


@pytest.mark.api
async def test_api_publishes_in_memory_cron_store_as_reference_implementation() -> None:
    from myrm_agent_harness.api import CronStore, InMemoryCronStore

    store = InMemoryCronStore()

    assert isinstance(store, CronStore)
    assert await store.get_job("missing-job") is None
    assert await store.list_jobs() == []


@pytest.mark.api
async def test_api_publishes_in_memory_kanban_store_as_reference_implementation() -> None:
    from myrm_agent_harness.api import InMemoryKanbanStore, KanbanStore

    store = InMemoryKanbanStore()

    assert issubclass(InMemoryKanbanStore, KanbanStore)
    assert await store.get_board("missing-board") is None


@pytest.mark.api
def test_create_skill_agent_is_async_factory() -> None:
    from myrm_agent_harness.api import create_skill_agent

    assert callable(create_skill_agent)
    assert inspect.iscoroutinefunction(create_skill_agent)


@pytest.mark.api
def test_distribution_helpers_report_source_mode_in_dev() -> None:
    from myrm_agent_harness.api import get_distribution_mode, is_compiled_distribution
    from myrm_agent_harness.runtime.install_guard import DistributionMode

    assert get_distribution_mode() is DistributionMode.SOURCE
    assert is_compiled_distribution() is False
