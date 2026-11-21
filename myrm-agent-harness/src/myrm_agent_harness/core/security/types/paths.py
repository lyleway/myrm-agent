"""Path access types: the access roots a session was granted and the path policy file operations are checked by.

[INPUT]
- (none — pure types, zero dependencies; the forbidden-path defaults are read lazily from ``core.security.path``)

[OUTPUT]
- AccessRoot / access_roots_from_paths: a directory root granted for file operations
- PathPolicy: Layer 2.5 path access control

[POS]
The path half of a session's security configuration. Imports nothing from the security subsystem at load time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _default_dangerous_paths() -> frozenset[str]:
    from myrm_agent_harness.core.security.path import DANGEROUS_PATHS

    return DANGEROUS_PATHS


@dataclass(frozen=True, slots=True)
class AccessRoot:
    """A directory root granted for file operations within a session."""

    path: str
    writable: bool = True
    label: str = ""
    source: str = "declared"


def access_roots_from_paths(
    paths: tuple[str, ...],
    *,
    writable: bool = True,
    source: str = "declared",
) -> tuple[AccessRoot, ...]:
    return tuple(AccessRoot(path=p, writable=writable, source=source) for p in paths)


@dataclass(frozen=True, slots=True)
class PathPolicy:
    """Path-level access control for file operations.

    Three-layer check: forbidden → access_roots → workspace; ``forbidden_paths`` always wins and defaults to
    ``security.path_security.DANGEROUS_PATHS``, the single source of truth for path-based security.

    ``workspace_label`` is an optional human-readable workspace label the GUI shows.

    ``allow_directory_grants`` is False for a host that keeps a run inside its workspace and declared roots
    (an isolated worktree): a path outside them is refused outright, and no folder access is offered or granted.
    """

    forbidden_paths: frozenset[str] = field(default_factory=_default_dangerous_paths)
    access_roots: tuple[AccessRoot, ...] = ()
    workspace_label: str | None = None
    allow_directory_grants: bool = True

    @property
    def allowed_roots(self) -> tuple[str, ...]:
        """Normalized path strings for access roots (read-only view)."""
        return tuple(r.path for r in self.access_roots)


def _default_path_policy() -> PathPolicy:
    return PathPolicy()
