"""The copies' folder is no way out of the workspace, not even when its name is swapped for a link while a copy is written.

[INPUT]
- app.ai_agents.media_tools.workspace_export::WorkspaceExporter (POS: completes a result with the workspace copies of its media)
- myrm_agent_harness.core.security.path::open_directory_beneath, ConfinedDirectory (POS: exclusive file creation below a trusted directory, decided on descriptors so no link redirects it)
- tests.unit.ai_agents.media_tools.conftest::library, workspace (POS: a real media library on a throwaway database and local storage; an empty workspace folder)

[OUTPUT]
Fails when a link that takes the place of the copies' folder, before or during a copy, receives a file or has one of its
files rewritten, when the swap leaves a partial file behind, when a file in the folder's place is overwritten or goes
unreported, or when a workspace named by a relative path or reached through a link stops working.

[POS]
tests/unit/ai_agents/media_tools/test_workspace_export_confinement.py; the agent's sandbox writes the workspace while the
server copies into it, so every step of the copy is tried with the swap made inside it (the swap is injected at that step,
so the outcome never depends on timing).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from unittest.mock import patch

import pytest
from myrm_agent_harness.core.security.path import ConfinedDirectory, open_directory_beneath

from app.ai_agents.media_tools.workspace_export import WORKSPACE_MEDIA_DIR, WorkspaceExporter
from app.core.media.reference import media_reference
from tests.support.media_library_env import MediaLibraryEnv

pytestmark = pytest.mark.skipif(os.name == "nt", reason="creating symbolic links needs a privilege on Windows")

_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
_THE_USERS = b"the user's own file"
_SWAPPED_OUT = "swapped-out"


@pytest.fixture
def elsewhere(tmp_path: Path) -> Path:
    """A folder outside the workspace that holds a file of the user's own, which must come out of every test unchanged."""
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / ".gitignore").write_bytes(_THE_USERS)
    return folder


def _exporter(workspace: Path) -> WorkspaceExporter:
    return WorkspaceExporter(workspace, user_id="user-1", chat_id="chat-1")


def _image_result(media_id: str) -> dict[str, object]:
    return {"images": [{"url": media_reference(media_id), "mime_type": "image/png"}], "model": "m"}


def _names(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


def _swap_for_a_link(workspace: Path, elsewhere: Path) -> None:
    """What an agent watching its workspace does: the copies' folder moves aside and a link to ``elsewhere`` takes its name."""
    folder = workspace / WORKSPACE_MEDIA_DIR
    folder.rename(workspace / _SWAPPED_OUT)
    folder.symlink_to(elsewhere, target_is_directory=True)


@contextmanager
def _swap_before_the_first_write(workspace: Path, elsewhere: Path) -> Iterator[None]:
    """The swap lands right after the exporter has opened (or created) its folder, before it writes anything there."""

    def open_then_swap(root: Path, parts: Sequence[str], *, create: bool = True) -> ConfinedDirectory:
        folder = open_directory_beneath(root, parts, create=create)
        _swap_for_a_link(workspace, elsewhere)
        return folder

    with patch("app.ai_agents.media_tools.workspace_export.open_directory_beneath", open_then_swap):
        yield


@contextmanager
def _swap_while_copying(workspace: Path, elsewhere: Path) -> Iterator[None]:
    """The swap lands after the temporary file is created and before the copy is published."""
    copyfileobj = shutil.copyfileobj

    def swap_then_copy(reader: BinaryIO, writer: BinaryIO) -> None:
        _swap_for_a_link(workspace, elsewhere)
        copyfileobj(reader, writer)

    with patch("app.ai_agents.media_tools.workspace_export.shutil.copyfileobj", swap_then_copy):
        yield


class TestWhatStandsInThePlaceOfTheFolder:
    async def test_a_link_is_refused_and_receives_nothing(
        self, library: MediaLibraryEnv, workspace: Path, elsewhere: Path
    ) -> None:
        record = await library.store(_PNG)
        (workspace / WORKSPACE_MEDIA_DIR).symlink_to(elsewhere, target_is_directory=True)

        answer = await _exporter(workspace).complete(_image_result(record.id))

        assert answer["workspace_files_error"] == (
            f"media://{record.id}: the copy could not be written ({WORKSPACE_MEDIA_DIR} is a link, not a directory)"
        )
        assert "workspace_files" not in answer
        assert _names(elsewhere) == [".gitignore"]
        assert (elsewhere / ".gitignore").read_bytes() == _THE_USERS

    async def test_a_file_is_reported_and_left_as_it_is(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        (workspace / WORKSPACE_MEDIA_DIR).write_bytes(b"not a folder")

        answer = await _exporter(workspace).complete(_image_result(record.id))

        assert "the copy could not be written" in str(answer["workspace_files_error"])
        assert "workspace_files" not in answer
        assert (workspace / WORKSPACE_MEDIA_DIR).read_bytes() == b"not a folder"


class TestSwappingTheFolderWhileACopyIsWritten:
    @pytest.mark.parametrize("folder_exists", [False, True], ids=["folder_it_creates", "folder_of_the_user"])
    async def test_a_swap_before_the_first_write_redirects_nothing(
        self, library: MediaLibraryEnv, workspace: Path, elsewhere: Path, folder_exists: bool
    ) -> None:
        record = await library.store(_PNG)
        if folder_exists:
            (workspace / WORKSPACE_MEDIA_DIR).mkdir()

        with _swap_before_the_first_write(workspace, elsewhere):
            answer = await _exporter(workspace).complete(_image_result(record.id))

        assert "the copy could not be written" in str(answer["workspace_files_error"])
        assert "workspace_files" not in answer
        assert _names(elsewhere) == [".gitignore"]
        assert (elsewhere / ".gitignore").read_bytes() == _THE_USERS
        # What was written before the swap went to the folder that was opened, and nothing was left half done in it.
        assert _names(workspace / _SWAPPED_OUT) == ([] if folder_exists else [".gitignore"])

    async def test_a_swap_while_the_bytes_are_copied_redirects_nothing(
        self, library: MediaLibraryEnv, workspace: Path, elsewhere: Path
    ) -> None:
        record = await library.store(_PNG)

        with _swap_while_copying(workspace, elsewhere):
            answer = await _exporter(workspace).complete(_image_result(record.id))

        assert "the copy could not be written" in str(answer["workspace_files_error"])
        assert "workspace_files" not in answer
        assert _names(elsewhere) == [".gitignore"]
        assert (elsewhere / ".gitignore").read_bytes() == _THE_USERS
        assert _names(workspace / _SWAPPED_OUT) == [".gitignore"]


class TestHowTheWorkspaceIsNamed:
    async def test_a_workspace_reached_through_a_link_works(
        self, library: MediaLibraryEnv, workspace: Path, tmp_path: Path
    ) -> None:
        record = await library.store(_PNG)
        link = tmp_path / "workspace-link"
        link.symlink_to(workspace, target_is_directory=True)

        answer = await _exporter(link).complete(_image_result(record.id))

        assert answer["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.png"]
        assert (workspace / WORKSPACE_MEDIA_DIR / f"{record.id}.png").read_bytes() == _PNG

    async def test_a_relative_workspace_works(
        self, library: MediaLibraryEnv, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = await library.store(_PNG)
        monkeypatch.chdir(workspace.parent)

        answer = await _exporter(Path(workspace.name)).complete(_image_result(record.id))

        assert answer["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.png"]
        assert (workspace / WORKSPACE_MEDIA_DIR / f"{record.id}.png").read_bytes() == _PNG
