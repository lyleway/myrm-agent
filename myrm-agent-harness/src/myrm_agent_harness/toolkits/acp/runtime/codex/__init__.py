"""Codex runtime backend — one resident ``codex app-server`` process, driven over its JSON-RPC protocol.

[INPUT]
- toolkits.acp.runtime.codex.runtime::CodexRuntime (POS: Codex runtime backend — a thread per conversation and a turn per prompt over the app server.) [lazy]

[OUTPUT]
- CodexRuntime: lazy-imported RuntimeBackend for Codex

[POS]
App-server backend of the runtime layer. Facade of the ``codex`` package, whose modules are imported by their own path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import CodexRuntime

__all__ = ["CodexRuntime"]


def __getattr__(name: str) -> object:
    if name == "CodexRuntime":
        from .runtime import CodexRuntime

        globals()[name] = CodexRuntime
        return CodexRuntime

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
