"""The folder of the workspace copies: kept out of git, left as the user has it, and never left half made, whoever else is at work.

[INPUT]
- app.ai_agents.media_tools.workspace_export::WorkspaceExporter (POS: completes a result with the workspace copies of its media)
- myrm_agent_harness.core.security.path::ConfinedDirectory (POS: exclusive file creation below a trusted directory, decided on descriptors so no link redirects it)
- tests.unit.ai_agents.media_tools.conftest::library, workspace (POS: a real media library on a throwaway database and local storage; an empty workspace folder)

[OUTPUT]
Fails when the folder reaches git, when a folder of the user's is written to, when a folder is left behind without its rule,
when a rule that something else wrote is replaced or removed, when what another process left in the folder is deleted,
when calls that meet while the folder is made leave copies without their rule, or when calls on different workspaces wait
for each other.

[POS]
tests/unit/ai_agents/media_tools/test_workspace_export_folder.py; the media library is real (SQLite in memory, local
storage in tmp), and each moment at which a rule cannot be written or another call arrives is injected at the step it
concerns.
"""

from __future__ import annotations

import asyncio
import errno
import os
import shutil
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from myrm_agent_harness.core.security.path import ConfinedDirectory

from app.ai_agents.media_tools.workspace_export import WORKSPACE_MEDIA_DIR, WorkspaceExporter
from app.core.media.reference import media_reference
from tests.support.media_library_env import MediaLibraryEnv

_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))


def _exporter(workspace: Path) -> WorkspaceExporter:
    return WorkspaceExporter(workspace, user_id="user-1", chat_id="chat-1")


def _image_result(media_id: str) -> dict[str, object]:
    return {"images": [{"url": media_reference(media_id), "mime_type": "image/png"}], "model": "m"}


def _names(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


class TestTheFolder:
    async def test_it_ignores_itself_in_git(self, library: MediaLibraryEnv, workspace: Path) -> None:
        if shutil.which("git") is None:
            pytest.skip("git is not installed")
        record = await library.store(_PNG)

        def git(*arguments: str) -> str:
            return subprocess.run(["git", "-C", str(workspace), *arguments], capture_output=True, text=True, check=True).stdout

        await asyncio.to_thread(git, "init", "-q")
        await _exporter(workspace).complete(_image_result(record.id))

        assert (workspace / WORKSPACE_MEDIA_DIR / f"{record.id}.png").is_file()
        assert await asyncio.to_thread(git, "status", "--porcelain") == ""
        await asyncio.to_thread(git, "add", "-A")
        assert await asyncio.to_thread(git, "diff", "--cached", "--name-only") == ""

    async def test_a_folder_of_the_user_is_left_as_it_is(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        folder = workspace / WORKSPACE_MEDIA_DIR
        folder.mkdir()
        (folder / "notes.txt").write_bytes(b"mine")

        answer = await _exporter(workspace).complete(_image_result(record.id))

        assert answer["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.png"]
        assert (folder / "notes.txt").read_bytes() == b"mine"
        assert not (folder / ".gitignore").exists()


class TestAFolderThatCannotBeGivenItsRule:
    async def test_it_is_not_left_behind(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)

        with patch.object(ConfinedDirectory, "create_new", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            answer = await _exporter(workspace).complete(_image_result(record.id))

        assert "No space left on device" in str(answer["workspace_files_error"])
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    async def test_a_rule_it_made_but_could_not_write_goes_with_it(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        create_new = ConfinedDirectory.create_new

        def create_a_rule_that_refuses_writes(folder: ConfinedDirectory, name: str) -> int:
            if name != ".gitignore":
                return create_new(folder, name)
            os.close(create_new(folder, name))  # the rule exists now, but the descriptor this call is handed cannot write
            return os.open(folder.path / name, os.O_RDONLY)

        with patch.object(ConfinedDirectory, "create_new", create_a_rule_that_refuses_writes):
            answer = await _exporter(workspace).complete(_image_result(record.id))

        assert "workspace_files" not in answer
        assert "the copy could not be written" in str(answer["workspace_files_error"])
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    async def test_what_another_process_left_in_it_stays(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        create_new = ConfinedDirectory.create_new

        def run_out_of_descriptors_after_another_process_has_written(folder: ConfinedDirectory, name: str) -> int:
            if name != ".gitignore":
                return create_new(folder, name)
            (folder.path / name).write_bytes(b"*\n")  # another process has made the rule ...
            (folder.path / "media_other.png").write_bytes(_PNG)  # ... and has put a copy next to it
            raise OSError(errno.EMFILE, "Too many open files")

        with patch.object(ConfinedDirectory, "create_new", run_out_of_descriptors_after_another_process_has_written):
            answer = await _exporter(workspace).complete(_image_result(record.id))

        assert "Too many open files" in str(answer["workspace_files_error"])
        folder = workspace / WORKSPACE_MEDIA_DIR
        assert _names(folder) == [".gitignore", "media_other.png"]
        assert (folder / "media_other.png").read_bytes() == _PNG

    async def test_a_rule_that_another_process_wrote_first_is_kept(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        create_new = ConfinedDirectory.create_new

        def lose_the_race(folder: ConfinedDirectory, name: str) -> int:
            if name == ".gitignore":
                (folder.path / name).write_bytes(b"*\n")  # another process wrote the rule just before this call
            return create_new(folder, name)

        with patch.object(ConfinedDirectory, "create_new", lose_the_race):
            answer = await _exporter(workspace).complete(_image_result(record.id))

        assert answer["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.png"]
        assert _names(workspace / WORKSPACE_MEDIA_DIR) == [".gitignore", f"{record.id}.png"]


class TestCallsThatMeetWhileTheFolderIsMade:
    async def test_the_copies_of_the_other_call_never_stand_without_the_rule(
        self, library: MediaLibraryEnv, workspace: Path
    ) -> None:
        first, second = await library.store(_PNG), await library.store(_PNG)
        create_new = ConfinedDirectory.create_new
        rule_made = threading.Event()
        copy_started = threading.Event()

        def make_the_first_rule_then_fail_to_write_it(folder: ConfinedDirectory, name: str) -> int:
            if name != ".gitignore":
                descriptor = create_new(folder, name)
                copy_started.set()
                return descriptor
            if rule_made.is_set():
                return create_new(folder, name)
            os.close(create_new(folder, name))
            rule_made.set()
            # A call that does not wait for this one starts copying within this time; one that waits cannot start before
            # this call is through.
            copy_started.wait(timeout=0.2)
            return os.open(folder.path / name, os.O_RDONLY)  # the descriptor this call is handed cannot write

        exporter = _exporter(workspace)
        with patch.object(ConfinedDirectory, "create_new", make_the_first_rule_then_fail_to_write_it):
            answers = await asyncio.gather(
                exporter.complete(_image_result(first.id)), exporter.complete(_image_result(second.id))
            )

        # One call fails to give the folder its rule and takes the folder away; the other then makes it anew.
        assert sum("workspace_files_error" in answer for answer in answers) == 1
        names = _names(workspace / WORKSPACE_MEDIA_DIR)
        assert len(names) == 2
        assert names[0] == ".gitignore"

    async def test_a_call_on_another_workspace_does_not_wait_for_them(
        self, library: MediaLibraryEnv, workspace: Path, tmp_path: Path
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        mine, theirs = await library.store(_PNG), await library.store(_PNG)
        create_new = ConfinedDirectory.create_new
        making_the_folder = threading.Event()
        other_call_done = threading.Event()
        held_up: list[bool] = []

        def stay_in_the_middle_of_making_the_folder(folder: ConfinedDirectory, name: str) -> int:
            if name == ".gitignore" and folder.path.parent == workspace.resolve():
                making_the_folder.set()
                held_up.append(not other_call_done.wait(timeout=5))
            return create_new(folder, name)

        with patch.object(ConfinedDirectory, "create_new", stay_in_the_middle_of_making_the_folder):
            making = asyncio.create_task(_exporter(workspace).complete(_image_result(mine.id)))
            assert await asyncio.to_thread(making_the_folder.wait, 5)
            other = await _exporter(elsewhere).complete(_image_result(theirs.id))
            other_call_done.set()
            await making

        assert "workspace_files" in other
        assert held_up == [False]
