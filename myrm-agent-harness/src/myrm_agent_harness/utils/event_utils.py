"""Custom events on the run's LangGraph ``custom`` stream.

``stream_mode="custom"`` carries only what is written through the run's StreamWriter. LangChain's own
``dispatch_custom_event`` reaches ``astream_events`` callbacks and never this stream, so every emitter of the harness
goes through here.

[INPUT]
- langchain_core.runnables::RunnableConfig, var_child_runnable_config (POS: the active run's config, which carries its StreamWriter)

[OUTPUT]
- resolve_tool_call_id_from_config: the tool_call_id of the active tool call
- dispatch_custom_event: async emitter (StreamWriter first, LangChain callbacks as the fallback)
- emit_custom_event_sync: sync emitter for synchronous tools (StreamWriter only; silent outside a run)

[POS]
The one place agent code puts a named custom event on the run's stream; tests/architecture/test_custom_event_single_source.py keeps LangChain's emitters out of every other module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config

if TYPE_CHECKING:
    from langgraph.types import StreamWriter


def resolve_tool_call_id_from_config(config: RunnableConfig | None) -> str | None:
    """Extract the active tool_call_id from LangGraph/LangChain RunnableConfig."""
    if config is None:
        config = var_child_runnable_config.get()
    if not config:
        return None
    metadata = config.get("metadata")
    if isinstance(metadata, dict):
        for key in ("tool_call_id", "langgraph_tool_call_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        for key in ("tool_call_id", "langgraph_tool_call_id", "__tool_call_id"):
            value = configurable.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _stream_writer(config: RunnableConfig | None) -> StreamWriter | None:
    """The StreamWriter of the run *config* belongs to; None when there is no config or it carries no writer."""
    if not config:
        return None
    runtime = config.get("configurable", {}).get("__pregel_runtime")
    writer = getattr(runtime, "stream_writer", None)
    return writer if callable(writer) else None


async def dispatch_custom_event(name: str, data: object, config: RunnableConfig | None = None) -> None:
    """Emit a custom event from async code: through the run's StreamWriter, else through LangChain's callbacks."""
    config = config or var_child_runnable_config.get()
    writer = _stream_writer(config)
    if writer is not None:
        try:
            writer({"name": name, "data": data})
            return
        except Exception:
            pass

    from langchain_core.callbacks.manager import adispatch_custom_event

    await adispatch_custom_event(name, data, config=config)


def emit_custom_event_sync(name: str, data: object) -> None:
    """Emit a custom event from synchronous code, such as a sync tool the graph runs in a worker thread.

    The thread carries the run's config, so the StreamWriter is found the same way as for the async emitter. Outside a
    run there is nothing to write to and the call returns silently; LangChain's synchronous emitter is no fallback,
    because its events never reach the ``custom`` stream.
    """
    writer = _stream_writer(var_child_runnable_config.get())
    if writer is not None:
        writer({"name": name, "data": data})
