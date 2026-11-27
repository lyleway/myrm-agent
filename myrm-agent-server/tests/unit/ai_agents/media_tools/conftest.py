"""Fixtures of the media tool tests: a real media library and an empty workspace folder.

[INPUT]
- tests.support.media_library_env::MediaLibraryEnv, media_library_env (POS: a real media library on a throwaway database and local storage)

[OUTPUT]
- library: the media library of one test, its database and local storage under the test's temporary directory
- workspace: an empty folder that stands in for an agent's workspace

[POS]
tests/unit/ai_agents/media_tools/conftest.py; every module of this directory that stores media or copies it into a
workspace takes these two fixtures instead of building its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.support.media_library_env import MediaLibraryEnv, media_library_env


@pytest.fixture
async def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MediaLibraryEnv]:
    async with media_library_env(tmp_path / "storage", monkeypatch) as env:
        yield env


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root
