"""A folder whose own name ends in a space is granted, saved and reloaded as that folder, never as the one above it."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from myrm_agent_harness.agent.security.session_access import get_session_access_roots, set_session_access_roots
from myrm_agent_harness.agent.security.types import AccessRoot

from app.services.agent.session_access_service import (
    access_roots_from_json,
    access_roots_to_json,
    apply_directory_resume_grant,
    bootstrap_session_access_roots,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot create names that end in a space")


@pytest.fixture(autouse=True)
def _reset_session_roots() -> Iterator[None]:
    set_session_access_roots(())
    yield
    set_session_access_roots(())


@pytest.fixture
def workspace_and_folder(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace and, beside it, a folder to grant whose own name ends in a space."""
    workspace = tmp_path / "ws"
    folder = tmp_path / "shared "
    workspace.mkdir()
    folder.mkdir()
    return workspace, folder.resolve()


@pytest.mark.asyncio
async def test_the_card_grants_the_folder_it_offered(workspace_and_folder: tuple[Path, Path]) -> None:
    workspace, folder = workspace_and_folder
    offered = {"path": str(folder), "writable": False}
    resume = {"decisions": [{"type": "approve", "extensions": {"grantDirectory": True, "grantDirectories": [offered]}}]}

    await apply_directory_resume_grant(None, resume, workspace_dir=str(workspace))

    assert [root.path for root in get_session_access_roots()] == [str(folder)]


def test_a_saved_root_reloads_as_the_folder_that_was_saved(workspace_and_folder: tuple[Path, Path]) -> None:
    workspace, folder = workspace_and_folder
    saved = access_roots_to_json((AccessRoot(path=str(folder), writable=False),))

    assert [root.path for root in access_roots_from_json(saved)] == [str(folder)]
    assert [root.path for root in bootstrap_session_access_roots(saved, workspace_dir=str(workspace))] == [str(folder)]
