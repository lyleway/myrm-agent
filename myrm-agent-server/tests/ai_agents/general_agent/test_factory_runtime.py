"""``factory/runtime.py``: the sandbox runtime assembly and the channel-notify tool loader.

[INPUT]
- app.ai_agents.general_agent.factory.runtime (POS: third assembly phase, creates execution resources only)

[OUTPUT]
- What ``build_sandbox_runtime`` binds (executor, workspace, snapshot interceptor, offload callbacks, the
  sandbox-capable flag and which memory callbacks an agent gets) and that ``load_channel_notify_tool`` degrades
  instead of failing.

[POS]
tests/ai_agents/general_agent/test_factory_runtime.py
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from myrm_agent_harness.toolkits.memory.strategies.extraction_domain import DomainPreset, auto_detect_preset

from app.ai_agents.general_agent.factory.runtime import SandboxRuntime, build_sandbox_runtime, load_channel_notify_tool

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from myrm_agent_harness.toolkits.memory.manager import MemoryManager

    from app.ai_agents.general_agent.agent import GeneralAgent

RUNTIME_LOGGER = "app.ai_agents.general_agent.factory.runtime"


class _Executor:
    """Stands in for the code executor the factory creates; records every workspace root it was bound to."""

    def __init__(self) -> None:
        self.bound_roots: list[str] = []

    def bind_workspace(self, root: str) -> None:
        self.bound_roots.append(root)

    def get_executor_name(self) -> str:
        return "fake-executor"


class _Collaborators:
    """What ``build_sandbox_runtime`` reaches outside the process for, replaced by recorders."""

    def __init__(self) -> None:
        self.executor = _Executor()
        self.quota = object()
        self.network_preferences: list[bool | None] = []
        self.interceptors: list[object] = []
        self.offload_calls: list[tuple[str, object, object]] = []


@pytest.fixture
def collaborators(monkeypatch: pytest.MonkeyPatch) -> _Collaborators:
    recorded = _Collaborators()

    def build_config(allow_network: bool | None) -> str:
        recorded.network_preferences.append(allow_network)
        return "execution-config"

    def create_executor(config: object) -> _Executor:
        assert config == "execution-config"
        return recorded.executor

    async def get_quota_manager() -> object:
        return recorded.quota

    def offload_builder(name: str) -> object:
        def build(executor: object, *, quota_checker: object) -> str:
            recorded.offload_calls.append((name, executor, quota_checker))
            return name

        return build

    monkeypatch.setattr("app.ai_agents.general_agent.config_builders.build_execution_config", build_config)
    monkeypatch.setattr("myrm_agent_harness.toolkits.code_execution.factory.create_executor", create_executor)
    monkeypatch.setattr("app.platform_utils.get_quota_manager", get_quota_manager)
    monkeypatch.setattr(
        "myrm_agent_harness.runtime.context.offload.create_compress_offload_callback", offload_builder("compress")
    )
    monkeypatch.setattr(
        "myrm_agent_harness.runtime.context.offload.create_context_snapshot_callback", offload_builder("snapshot")
    )
    monkeypatch.setattr("app.services.checkpoint.snapshot_service.get_snapshot_interceptor", lambda: "interceptor")
    monkeypatch.setattr(
        "myrm_agent_harness.toolkits.code_execution.interceptor.set_execution_interceptor", recorded.interceptors.append
    )
    return recorded


def _wrapper(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "code_execution_allow_network": None,
        "memory_extraction_preset": DomainPreset.RESEARCH.value,
        "user_instructions": None,
        "enable_shell_tools": False,
        "enable_external_cli": False,
        "declared_capabilities": (),
        "enable_memory_auto_extraction": True,
        "channel_name": "webui",
        "_lite_llm": None,
        "enable_memory": True,
        "incognito_mode": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _memory_manager() -> SimpleNamespace:
    """The part of ``MemoryManager`` the callbacks are built from: a manager with no policy restrictions."""
    return SimpleNamespace(memory_policy=None)


async def _build(
    wrapper: SimpleNamespace,
    *,
    workspace_root: str | None = "/work",
    llm: object | None = None,
    memory_manager: SimpleNamespace | None = None,
) -> SandboxRuntime:
    return await build_sandbox_runtime(
        cast("GeneralAgent", wrapper),
        llm=cast("BaseChatModel", llm if llm is not None else object()),
        memory_manager=cast("MemoryManager | None", memory_manager),
        effective_chat_id="chat-1",
        workspace_root=workspace_root,
    )


def _memory_callbacks(runtime: SandboxRuntime) -> dict[str, bool]:
    """Which of the four memory callbacks the runtime ended up with."""
    return {
        "eviction": runtime.compress_eviction_cb is not None,
        "pre_compact": runtime.pre_compact_cb is not None,
        "archive_store": runtime.archive_checkpoint_store is not None,
        "archive_notifier": runtime.archive_checkpoint_notifier is not None,
    }


ALL_MEMORY_CALLBACKS = dict.fromkeys(("eviction", "pre_compact", "archive_store", "archive_notifier"), True)
NO_MEMORY_CALLBACKS = dict.fromkeys(ALL_MEMORY_CALLBACKS, False)


@pytest.mark.asyncio
async def test_the_executor_is_bound_to_the_workspace_and_kept_on_the_agent(collaborators: _Collaborators) -> None:
    wrapper = _wrapper()

    runtime = await _build(wrapper, workspace_root="/work")

    assert runtime.executor is collaborators.executor
    assert wrapper._executor is collaborators.executor
    assert collaborators.executor.bound_roots == ["/work"]


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_root", [None, ""])
async def test_no_workspace_root_leaves_the_executor_unbound(collaborators: _Collaborators, workspace_root: str | None) -> None:
    await _build(_wrapper(), workspace_root=workspace_root)

    assert collaborators.executor.bound_roots == []


@pytest.mark.asyncio
async def test_the_network_preference_reaches_the_execution_config(collaborators: _Collaborators) -> None:
    await _build(_wrapper(code_execution_allow_network=False))

    assert collaborators.network_preferences == [False]


@pytest.mark.asyncio
async def test_the_offload_callbacks_share_the_executor_and_the_quota_manager(collaborators: _Collaborators) -> None:
    runtime = await _build(_wrapper())

    executor, quota = collaborators.executor, collaborators.quota
    assert collaborators.offload_calls == [("compress", executor, quota), ("snapshot", executor, quota)]
    assert (runtime.compress_offload_cb, runtime.context_snapshot_cb) == ("compress", "snapshot")


@pytest.mark.asyncio
async def test_the_snapshot_interceptor_is_registered(collaborators: _Collaborators) -> None:
    await _build(_wrapper())

    assert collaborators.interceptors == ["interceptor"]


@pytest.mark.asyncio
async def test_a_failing_snapshot_interceptor_does_not_stop_the_runtime(
    collaborators: _Collaborators, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> object:
        raise RuntimeError("snapshot service down")

    monkeypatch.setattr("app.services.checkpoint.snapshot_service.get_snapshot_interceptor", broken)

    with caplog.at_level(logging.WARNING, logger=RUNTIME_LOGGER):
        runtime = await _build(_wrapper())

    assert runtime.executor is collaborators.executor
    assert collaborators.interceptors == []
    assert "Failed to register snapshot interceptor: snapshot service down" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flags", "sandbox_capable"),
    [
        ({"enable_shell_tools": True}, True),
        ({"enable_external_cli": True}, True),
        ({"declared_capabilities": ("coding",)}, True),
        ({"declared_capabilities": ("research",)}, False),
        ({}, False),
    ],
)
async def test_sandbox_capability_follows_the_tools_and_the_declared_capabilities(
    collaborators: _Collaborators, flags: dict[str, object], sandbox_capable: bool
) -> None:
    runtime = await _build(_wrapper(**flags))

    assert runtime.memory_ext.is_sandbox_capable is sandbox_capable


@pytest.mark.asyncio
async def test_an_auto_preset_is_resolved_from_the_user_instructions(collaborators: _Collaborators) -> None:
    instructions = "You are a research assistant that reads papers and keeps notes"

    runtime = await _build(_wrapper(memory_extraction_preset="auto", user_instructions=instructions))

    assert runtime.memory_ext.memory_extraction_preset == auto_detect_preset(instructions).value
    assert runtime.memory_ext.memory_extraction_preset != "auto"


@pytest.mark.asyncio
async def test_an_auto_preset_without_instructions_resolves_to_none(collaborators: _Collaborators) -> None:
    runtime = await _build(_wrapper(memory_extraction_preset="auto", user_instructions=None))

    assert runtime.memory_ext.memory_extraction_preset == DomainPreset.NONE.value


@pytest.mark.asyncio
async def test_an_explicit_preset_is_kept(collaborators: _Collaborators) -> None:
    runtime = await _build(_wrapper(memory_extraction_preset=DomainPreset.PERSONA.value, user_instructions="research"))

    assert runtime.memory_ext.memory_extraction_preset == DomainPreset.PERSONA.value


@pytest.mark.asyncio
async def test_the_extractor_uses_the_lite_model_and_falls_back_to_the_main_one(collaborators: _Collaborators) -> None:
    main, lite = object(), object()

    without_lite = await _build(_wrapper(_lite_llm=None), llm=main)
    with_lite = await _build(_wrapper(_lite_llm=lite), llm=main)

    assert without_lite.memory_ext.extractor_llm is main
    assert with_lite.memory_ext.extractor_llm is lite


@pytest.mark.asyncio
async def test_an_agent_with_memory_gets_every_memory_callback(collaborators: _Collaborators) -> None:
    runtime = await _build(_wrapper(), memory_manager=_memory_manager())

    assert _memory_callbacks(runtime) == ALL_MEMORY_CALLBACKS


@pytest.mark.asyncio
async def test_without_a_memory_manager_no_memory_callback_is_built(collaborators: _Collaborators) -> None:
    runtime = await _build(_wrapper())

    assert _memory_callbacks(runtime) == NO_MEMORY_CALLBACKS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flags", "callbacks"),
    [
        pytest.param(
            {"enable_memory_auto_extraction": False}, {**ALL_MEMORY_CALLBACKS, "eviction": False}, id="no-auto-extraction"
        ),
        pytest.param({"enable_shell_tools": True}, {**ALL_MEMORY_CALLBACKS, "eviction": False}, id="sandbox-capable"),
        pytest.param(
            {"incognito_mode": True},
            {**ALL_MEMORY_CALLBACKS, "archive_store": False, "archive_notifier": False},
            id="incognito",
        ),
        pytest.param(
            {"enable_memory": False},
            {**ALL_MEMORY_CALLBACKS, "pre_compact": False, "archive_store": False, "archive_notifier": False},
            id="memory-off",
        ),
        pytest.param({"is_subagent": True}, NO_MEMORY_CALLBACKS, id="subagent-flag"),
        pytest.param({"channel_name": "subagent"}, NO_MEMORY_CALLBACKS, id="subagent-channel"),
    ],
)
async def test_what_switches_the_memory_callbacks_off(
    collaborators: _Collaborators, flags: dict[str, object], callbacks: dict[str, bool]
) -> None:
    runtime = await _build(_wrapper(**flags), memory_manager=_memory_manager())

    assert _memory_callbacks(runtime) == callbacks


def test_the_notify_tool_is_appended_for_the_configured_destinations(caplog: pytest.LogCaptureFixture) -> None:
    wrapper = SimpleNamespace(notify_targets=({"channel": "telegram", "recipient_id": "chat_1"},), declared_allowed_roots=())
    tools: list[object] = []

    with caplog.at_level(logging.INFO, logger=RUNTIME_LOGGER):
        load_channel_notify_tool(cast("GeneralAgent", wrapper), tools)

    assert [getattr(tool, "name", None) for tool in tools] == ["channel_notify_tool"]
    assert "Loaded channel_notify_tool (1 destinations)" in caplog.text


def test_no_notify_tool_is_appended_without_destinations(caplog: pytest.LogCaptureFixture) -> None:
    wrapper = SimpleNamespace(notify_targets=(), declared_allowed_roots=())
    tools: list[object] = []

    with caplog.at_level(logging.INFO, logger=RUNTIME_LOGGER):
        load_channel_notify_tool(cast("GeneralAgent", wrapper), tools)

    assert tools == []
    assert "Loaded channel_notify_tool" not in caplog.text


def test_a_failing_notify_wiring_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("wiring broke")

    monkeypatch.setattr("app.services.agent.outbound_notify.factory_wiring.append_channel_notify_tool", broken)
    wrapper = SimpleNamespace(notify_targets=({"channel": "slack", "recipient_id": "C1"},), declared_allowed_roots=())
    tools: list[object] = []

    with caplog.at_level(logging.WARNING, logger=RUNTIME_LOGGER):
        load_channel_notify_tool(cast("GeneralAgent", wrapper), tools)

    assert tools == []
    assert "channel_notify_tool load failed (degraded): wiring broke" in caplog.text
