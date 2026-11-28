"""ACP (Agent Client Protocol) integration: Myrm delegating work to external agents.

- Runtime (runtime/): Connects to external agents via the ACP protocol, a CLI process or an app server.
- Drivers (drivers/): What each known agent is (``DriverSpec``) and the command line built from it.

Facades and contracts (root):
- types.py: Core type definitions (RuntimeBackend Protocol, RuntimeEvent, etc.)
- acp_agent_tools.py: Delegate-to-agent tool factory for the agent layer.

Shared infrastructure (core/):
- core/backend_detector.py: CLI backend auto-detection
- auth/: the login commands and login state of CLI backends (a credential is never held or relayed here)

The opposite direction, an IDE driving Myrm over ACP, is ``agent/acp``; nothing here depends on it.


[INPUT]
- runtime.pool::RuntimePool (POS: runtime pool management layer)
- types::RuntimeBackend, RuntimeConfig (POS: ACP runtime type definitions layer.)

[OUTPUT]
- RuntimePool: unified runtime instance pool (lazy import)
- RuntimeBackend: runtime backend protocol (lazy import)
- RuntimeConfig: runtime configuration model (lazy import)

[POS]
ACP toolkit entry point. Provides lazy-loaded access to the runtime components for delegating to external agents.
"""

from importlib import import_module as _import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime.pool import RuntimePool
    from .types import RuntimeBackend, RuntimeConfig

_EXPORTS: dict[str, tuple[str, str]] = {
    "RuntimeBackend": (".types", "RuntimeBackend"),
    "RuntimeConfig": (".types", "RuntimeConfig"),
    "RuntimePool": (".runtime.pool", "RuntimePool"),
}

__all__ = [
    "RuntimeBackend",
    "RuntimeConfig",
    "RuntimePool",
]


def __getattr__(name: str) -> object:
    try:
        module_path, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(_import_module(module_path, __name__), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__ + [name for name in globals() if not name.startswith("_")]))
