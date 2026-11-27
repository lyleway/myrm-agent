"""Safety metadata of MCP tools, keyed by the tool's prefixed name.

[INPUT]
- safety_table::SafetyMetadata (POS: tool safety attribute model)

[OUTPUT]
- MCPAnnotations: the claims a server made about a tool that make a person more careful (destructive, open world)
- register_ptc_safety_metadata / get_ptc_safety_metadata / unregister_ptc_safety_metadata / evict_ptc_safety_metadata_by_prefix: thread-safe registration, lookup and eviction
- _PTC_TOOL_SAFETY / _PTC_LOCK: the one table read by ``resolve_safety_metadata``, and the lock guarding it

[POS]
Process-wide store filled when an MCP server's tools are discovered and read when a tool call is scheduled or
approved. The key is the prefixed name ``mcp__{server}__{tool}``, the identity the permission rules, the loop
guard and the connection pool use for a tool: a name has one record, and registering it again replaces the
record. A script's ``from <skill> import <function>`` reaches the same key through the proxy's own name
resolution, so scheduling, approval and execution agree on which tool a call is. ``registry.py`` re-exports
every symbol, so consumers import the store from that one flat module; the store lives in this file to keep
``registry.py`` under the line limit.
"""

from __future__ import annotations

import threading
from typing import TypedDict

from .safety_table import SafetyMetadata


class MCPAnnotations(TypedDict, total=False):
    """What a server claims about a tool that calls for more care; a claim that would reassure is never kept.

    Approval cards and review prompts show these, so a server's own statement that a tool is harmless can never
    reach the person or the model deciding whether to run it.
    """

    destructiveHint: bool
    openWorldHint: bool


_PTC_TOOL_SAFETY: dict[str, tuple[SafetyMetadata, MCPAnnotations]] = {}

# Thread lock protecting mutations of the table above
_PTC_LOCK = threading.RLock()


def register_ptc_safety_metadata(tool_name: str, safety_meta: SafetyMetadata, annotations: MCPAnnotations) -> None:
    """Record the safety metadata judged for an MCP tool, replacing any earlier record of the same name."""
    with _PTC_LOCK:
        _PTC_TOOL_SAFETY[tool_name] = (safety_meta, annotations)


def get_ptc_safety_metadata(tool_name: str) -> tuple[SafetyMetadata, MCPAnnotations] | None:
    """Retrieve the safety metadata recorded for an MCP tool."""
    with _PTC_LOCK:
        return _PTC_TOOL_SAFETY.get(tool_name)


def unregister_ptc_safety_metadata(tool_name: str) -> bool:
    """Drop the record of a single tool; True if it existed."""
    with _PTC_LOCK:
        return _PTC_TOOL_SAFETY.pop(tool_name, None) is not None


def evict_ptc_safety_metadata_by_prefix(prefix: str) -> int:
    """Drop every record whose tool name starts with ``prefix`` and return how many were dropped."""
    with _PTC_LOCK:
        doomed = [name for name in _PTC_TOOL_SAFETY if name.startswith(prefix)]
        for name in doomed:
            del _PTC_TOOL_SAFETY[name]
        return len(doomed)
