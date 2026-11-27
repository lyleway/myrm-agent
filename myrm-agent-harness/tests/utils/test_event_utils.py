"""Custom events go to the run's StreamWriter; LangChain's callbacks are only the async emitter's fallback.

The custom stream carries only what a StreamWriter receives, so an emitter that used LangChain's callbacks for a run that
has a writer would lose its event without any error. The run's config carries the writer and the active tool call's id;
these cases hand the emitters hand-made configs and the ambient config a running tool sees.

[INPUT]
- utils.event_utils::dispatch_custom_event, emit_custom_event_sync, resolve_tool_call_id_from_config (POS: the units under test)
- langchain_core.runnables.config::var_child_runnable_config (POS: the ambient run config both emitters read)

[OUTPUT]
- Cases: which path a config selects for the async emitter, silence of the sync emitter outside a run, and the order in
  which a tool call id is looked up.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config

from myrm_agent_harness.utils.event_utils import (
    dispatch_custom_event,
    emit_custom_event_sync,
    resolve_tool_call_id_from_config,
)

_LANGCHAIN_ASYNC_EMITTER = "langchain_core.callbacks.manager.adispatch_custom_event"
_LANGCHAIN_SYNC_EMITTER = "langchain_core.callbacks.manager.dispatch_custom_event"


def _run_config(writer: object) -> RunnableConfig:
    """The config of a run whose StreamWriter is *writer*."""
    return RunnableConfig(configurable={"__pregel_runtime": SimpleNamespace(stream_writer=writer)})


@contextmanager
def _ambient(config: RunnableConfig) -> Iterator[None]:
    """Make *config* the config of the running code, as the graph does for the tool it runs."""
    token = var_child_runnable_config.set(config)
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


_CONFIGS_WITHOUT_A_WRITER = [
    pytest.param(RunnableConfig(configurable={}), id="no-runtime"),
    pytest.param(RunnableConfig(configurable={"__pregel_runtime": SimpleNamespace()}), id="runtime-without-writer"),
    pytest.param(
        RunnableConfig(configurable={"__pregel_runtime": SimpleNamespace(stream_writer="not callable")}),
        id="writer-not-callable",
    ),
]


async def test_the_async_emitter_writes_the_event_to_the_runs_writer() -> None:
    writer = MagicMock()

    with patch(_LANGCHAIN_ASYNC_EMITTER, new_callable=AsyncMock) as langchain:
        await dispatch_custom_event("plan_update", {"revision": 1}, config=_run_config(writer))

    writer.assert_called_once_with({"name": "plan_update", "data": {"revision": 1}})
    langchain.assert_not_awaited()


async def test_the_async_emitter_reads_the_ambient_run_config_when_given_none() -> None:
    writer = MagicMock()

    with patch(_LANGCHAIN_ASYNC_EMITTER, new_callable=AsyncMock) as langchain, _ambient(_run_config(writer)):
        await dispatch_custom_event("status", "ok")

    writer.assert_called_once_with({"name": "status", "data": "ok"})
    langchain.assert_not_awaited()


@pytest.mark.parametrize("config", [pytest.param(None, id="no-config"), *_CONFIGS_WITHOUT_A_WRITER])
async def test_the_async_emitter_falls_back_to_langchain_callbacks_without_a_writer(
    config: RunnableConfig | None,
) -> None:
    with patch(_LANGCHAIN_ASYNC_EMITTER, new_callable=AsyncMock) as langchain:
        await dispatch_custom_event("status", "ok", config=config)

    langchain.assert_awaited_once_with("status", "ok", config=config)


async def test_the_async_emitter_falls_back_to_langchain_callbacks_when_the_writer_raises() -> None:
    writer = MagicMock(side_effect=RuntimeError("stream closed"))
    config = _run_config(writer)

    with patch(_LANGCHAIN_ASYNC_EMITTER, new_callable=AsyncMock) as langchain:
        await dispatch_custom_event("status", "ok", config=config)

    writer.assert_called_once()
    langchain.assert_awaited_once_with("status", "ok", config=config)


def test_the_sync_emitter_writes_to_the_writer_of_the_ambient_run() -> None:
    writer = MagicMock()

    with _ambient(_run_config(writer)):
        emit_custom_event_sync("plan_update", {"revision": 2})

    writer.assert_called_once_with({"name": "plan_update", "data": {"revision": 2}})


def test_the_sync_emitter_is_silent_outside_a_run() -> None:
    with patch(_LANGCHAIN_SYNC_EMITTER) as langchain:
        emit_custom_event_sync("plan_update", {"revision": 2})

    langchain.assert_not_called()


@pytest.mark.parametrize("config", _CONFIGS_WITHOUT_A_WRITER)
def test_the_sync_emitter_is_silent_for_a_run_without_a_writer(config: RunnableConfig) -> None:
    with _ambient(config), patch(_LANGCHAIN_SYNC_EMITTER) as langchain:
        emit_custom_event_sync("plan_update", {"revision": 2})

    langchain.assert_not_called()


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        pytest.param(RunnableConfig(metadata={"tool_call_id": " call-1 "}), "call-1", id="metadata-trimmed"),
        pytest.param(RunnableConfig(metadata={"langgraph_tool_call_id": "call-2"}), "call-2", id="metadata-langgraph"),
        pytest.param(RunnableConfig(configurable={"tool_call_id": "call-3"}), "call-3", id="configurable"),
        pytest.param(
            RunnableConfig(configurable={"langgraph_tool_call_id": "call-4"}), "call-4", id="configurable-langgraph"
        ),
        pytest.param(RunnableConfig(configurable={"__tool_call_id": "call-5"}), "call-5", id="configurable-private"),
        pytest.param(
            RunnableConfig(metadata={"tool_call_id": "meta"}, configurable={"tool_call_id": "conf"}),
            "meta",
            id="metadata-before-configurable",
        ),
        pytest.param(
            RunnableConfig(metadata={"tool_call_id": "  ", "langgraph_tool_call_id": "second"}),
            "second",
            id="blank-first-key-skipped",
        ),
        pytest.param(
            RunnableConfig(metadata={"tool_call_id": 7}, configurable={"tool_call_id": "conf"}),
            "conf",
            id="non-string-skipped",
        ),
        pytest.param(
            RunnableConfig(metadata={"tool_call_id": "  "}, configurable={"tool_call_id": ""}), None, id="all-blank"
        ),
        pytest.param(RunnableConfig(), None, id="empty-config"),
    ],
)
def test_the_tool_call_id_is_found_in_the_config(config: RunnableConfig, expected: str | None) -> None:
    assert resolve_tool_call_id_from_config(config) == expected


def test_the_tool_call_id_of_the_ambient_run_is_used_when_no_config_is_given() -> None:
    with _ambient(RunnableConfig(metadata={"tool_call_id": "call-9"})):
        assert resolve_tool_call_id_from_config(None) == "call-9"


def test_there_is_no_tool_call_id_outside_a_run() -> None:
    assert resolve_tool_call_id_from_config(None) is None
