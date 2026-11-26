"""Automatic detection of CLI agent backends.

Discovers the installed agents of the auth contract (claude, codex, gemini, qwen) by searching PATH,
common installation paths, and npm global. Maintains process-wide caches
for versioned and non-versioned detection results to avoid repeated scans.

[INPUT]
- myrm_agent_harness.toolkits.acp.toolchains::TOOLCHAIN_BASE_DIR (POS: Isolated toolchain manager for external CLI agents — keeps tool installations out of the global OS environment)
- myrm_agent_harness.toolkits.acp.auth.credential_store::CredentialState (POS: Credential persistence and detection layer for the ACP auth subsystem) [TYPE_CHECKING]
- myrm_agent_harness.toolkits.acp.auth._profiles::known_backends (POS: Authentication profile registry; the agents that have a contract)
- myrm_agent_harness.toolkits.acp.auth.launch_env::cli_base_env (POS: The environment every launch of a CLI starts from)
- myrm_agent_harness.toolkits.acp.core.probe::run_probe (POS: Process primitive of the detection and login-state checks)

[OUTPUT]
- DetectedBackend: A detected CLI backend with its path and version.
- BackendDetector: Detects available CLI agent backends.

[POS]
Automatic detection of CLI agent backends with process-wide cache sharing and
optional version probing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from myrm_agent_harness.toolkits.acp.auth._profiles import known_backends
from myrm_agent_harness.toolkits.acp.auth.launch_env import cli_base_env
from myrm_agent_harness.toolkits.acp.core.probe import run_probe
from myrm_agent_harness.toolkits.acp.toolchains import TOOLCHAIN_BASE_DIR

if TYPE_CHECKING:
    from collections.abc import Mapping

    from myrm_agent_harness.toolkits.acp.auth.credential_store import CredentialState

logger = logging.getLogger(__name__)

_COMMON_PATHS = (
    TOOLCHAIN_BASE_DIR / "bin",  # Check isolated toolchain first
    Path.home() / ".local" / "bin",
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
)


@dataclass(frozen=True, slots=True)
class DetectedBackend:
    """A detected CLI backend with its path and version."""

    name: str
    path: str
    version: str | None = None


class BackendDetector:
    """Detects available CLI agent backends.

    Searches for known CLI tools using ``shutil.which``, common paths,
    and npm global. Optionally runs ``--version`` to detect the version.

    Results are cached process-wide after the first detection, with a soft
    freshness TTL. Callers can force a fresh scan via ``refresh=True``.
    """

    _cache_with_version: ClassVar[list[DetectedBackend] | None] = None
    _cache_without_version: ClassVar[list[DetectedBackend] | None] = None
    _cache_with_version_at: ClassVar[float | None] = None
    _cache_without_version_at: ClassVar[float | None] = None
    _cache_ttl_seconds: ClassVar[float] = 300.0

    async def detect(self, *, include_version: bool = True, refresh: bool = False) -> list[DetectedBackend]:
        """Detect all available backends.

        Args:
            include_version: If True, run ``<cmd> --version`` to get version info.
            refresh: If True, bypass cache and force a fresh detection pass.

        Returns:
            List of detected backends (cached after first call).
        """
        now = time.monotonic()
        if not refresh:
            cached = self._get_cached(include_version=include_version, now=now)
            if cached is not None:
                return cached

            if include_version:
                cached_without_version = self._get_cached(include_version=False, now=now)
                if cached_without_version is not None:
                    hydrated = await self._hydrate_versions(cached_without_version)
                    self._set_cache(include_version=True, value=hydrated)
                    return hydrated
            else:
                cached_with_version = self._get_cached(include_version=True, now=now)
                if cached_with_version is not None:
                    stripped = self._strip_versions(cached_with_version)
                    self._set_cache(include_version=False, value=stripped)
                    return stripped

        located = [(name, path) for name in known_backends() if (path := self._find_executable(name)) is not None]
        versions = await self._versions([path for _, path in located]) if include_version else [None] * len(located)
        results = [
            DetectedBackend(name=name, path=path, version=version)
            for (name, path), version in zip(located, versions, strict=True)
        ]
        for found in results:
            logger.info("backend_detected name=%s path=%s version=%s", found.name, found.path, found.version)

        if include_version:
            self._set_cache(include_version=False, value=self._strip_versions(results))
        self._set_cache(include_version=include_version, value=results)
        return results

    async def detect_with_auth(
        self,
        *,
        env: Mapping[str, str] | None = None,
        include_version: bool = True,
        refresh: bool = False,
    ) -> list[tuple[DetectedBackend, CredentialState]]:
        """Detect installed backends and pair each with its current auth state.

        Installation detection is cached (it is effectively static for a process), but the login state is resolved
        fresh on every call because the user can sign in or out at any time: a CLI that can report its own state is
        asked, the others are judged by their credential file. Powers the settings page's "installed + logged-in" view.
        """
        from myrm_agent_harness.toolkits.acp.auth import CredentialStore
        from myrm_agent_harness.toolkits.acp.auth.login_state import resolve_login_state

        source = os.environ if env is None else env
        store = CredentialStore(env)
        detected = await self.detect(include_version=include_version, refresh=refresh)
        states = await asyncio.gather(
            *(
                resolve_login_state(store, found.name, executable=found.path, env=cli_base_env(found.path, source))
                for found in detected
            )
        )
        return list(zip(detected, states, strict=True))

    def invalidate_cache(self) -> None:
        """Force process-wide re-detection on next call."""
        type(self).invalidate_shared_cache()

    @classmethod
    def invalidate_shared_cache(cls) -> None:
        """Drop both versioned and non-versioned detection caches."""
        cls._cache_with_version = None
        cls._cache_without_version = None
        cls._cache_with_version_at = None
        cls._cache_without_version_at = None

    @classmethod
    def _get_cached(cls, *, include_version: bool, now: float) -> list[DetectedBackend] | None:
        cached = cls._cache_with_version if include_version else cls._cache_without_version
        cached_at = cls._cache_with_version_at if include_version else cls._cache_without_version_at
        if cached is None or cached_at is None:
            return None
        if now - cached_at > cls._cache_ttl_seconds:
            return None
        return cached

    @classmethod
    def _set_cache(cls, *, include_version: bool, value: list[DetectedBackend]) -> None:
        captured_at = time.monotonic()
        if include_version:
            cls._cache_with_version = value
            cls._cache_with_version_at = captured_at
            return
        cls._cache_without_version = value
        cls._cache_without_version_at = captured_at

    async def _hydrate_versions(self, backends: list[DetectedBackend]) -> list[DetectedBackend]:
        versions = await self._versions([backend.path for backend in backends])
        return [
            DetectedBackend(name=backend.name, path=backend.path, version=version)
            for backend, version in zip(backends, versions, strict=True)
        ]

    async def _versions(self, paths: list[str]) -> list[str | None]:
        """The version of every executable, asked at once: one slow CLI does not hold up the rest."""
        return list(await asyncio.gather(*(self._get_version(path) for path in paths)))

    @staticmethod
    def _strip_versions(backends: list[DetectedBackend]) -> list[DetectedBackend]:
        return [DetectedBackend(name=item.name, path=item.path) for item in backends]

    def _find_executable(self, name: str) -> str | None:
        """Find an executable by name using multiple strategies."""
        found = shutil.which(name)
        if found:
            return found

        for base_path in _COMMON_PATHS:
            candidate = base_path / name
            if candidate.is_file() and _is_executable(candidate):
                return str(candidate)

        npm_global = self._find_npm_global(name)
        if npm_global:
            return npm_global

        return None

    def _find_npm_global(self, name: str) -> str | None:
        """Check npm global bin directory."""
        npm_prefix = shutil.which("npm")
        if npm_prefix is None:
            return None
        npm_bin = Path(npm_prefix).parent
        candidate = npm_bin / name
        if candidate.is_file() and _is_executable(candidate):
            return str(candidate)
        return None

    async def _get_version(self, path: str) -> str | None:
        """Run ``<path> --version`` and extract version string."""
        result = await run_probe([path, "--version"], env=cli_base_env(path, os.environ), timeout_seconds=10.0)
        if result is None or result.returncode != 0:
            return None
        lines = result.stdout.splitlines()
        return lines[0].strip() if lines else None


def _is_executable(path: Path) -> bool:
    """Check if a path is executable."""
    try:
        return os.access(path, os.X_OK)
    except OSError:
        return False
