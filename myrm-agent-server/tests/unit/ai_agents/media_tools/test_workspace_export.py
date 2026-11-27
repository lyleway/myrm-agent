"""Copying generated media into the workspace: exact, idempotent, confined to its folder, never raising.

[INPUT]
- app.ai_agents.media_tools.workspace_export::WorkspaceExporter (POS: completes a result with the workspace copies of its media)
- tests.unit.ai_agents.media_tools.conftest::library, workspace (POS: a real media library on a throwaway database and local storage; an empty workspace folder)

[OUTPUT]
Fails when a copy differs from the stored file, is rewritten or clobbered by a repeat, can leave its folder, when a failure
is hidden instead of reported or raised to the caller, or when a cancellation is swallowed.

[POS]
tests/unit/ai_agents/media_tools/test_workspace_export.py; the media library is real (SQLite in memory, local storage in tmp).
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
from pathlib import Path
from typing import BinaryIO
from unittest.mock import patch

import pytest

from app.ai_agents.media_tools.workspace_export import WORKSPACE_MEDIA_DIR, WorkspaceExporter
from app.core.media.reference import local_media_file, media_reference
from tests.support.media_library_env import MediaLibraryEnv

_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
_LOGGER = "app.ai_agents.media_tools.workspace_export"


def _exporter(workspace: Path) -> WorkspaceExporter:
    return WorkspaceExporter(workspace, user_id="user-1", chat_id="chat-1")


def _image_result(media_id: str) -> dict[str, object]:
    return {"images": [{"url": media_reference(media_id), "mime_type": "image/png"}], "model": "m"}


class TestTheCopy:
    async def test_it_is_the_stored_file_byte_for_byte(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)

        answer = await _exporter(workspace).complete(_image_result(record.id))

        assert answer["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.png"]
        assert "workspace_files_error" not in answer
        assert (workspace / WORKSPACE_MEDIA_DIR / f"{record.id}.png").read_bytes() == _PNG

    async def test_it_leaves_the_result_it_completes_untouched(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        result = _image_result(record.id)

        completed = await _exporter(workspace).complete(result)

        assert "workspace_files" not in result
        assert {key: completed[key] for key in result} == result

    async def test_editing_it_cannot_reach_the_original(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        await _exporter(workspace).complete(_image_result(record.id))

        (workspace / WORKSPACE_MEDIA_DIR / f"{record.id}.png").write_bytes(b"edited in place")

        assert await library.storage.read(record.storage_key) == _PNG

    @pytest.mark.parametrize(
        ("content_type", "key", "extension"),
        [("video/mp4", "video_urls", "mp4"), ("audio/mpeg", "audio_url", "mp3"), ("image/jpeg", "images", "jpg")],
    )
    async def test_it_is_named_after_the_media_and_its_extension(
        self, library: MediaLibraryEnv, workspace: Path, content_type: str, key: str, extension: str
    ) -> None:
        record = await library.store(_PNG, content_type=content_type)
        reference = media_reference(record.id)
        results: dict[str, dict[str, object]] = {
            "video_urls": {"video_urls": [reference]},
            "audio_url": {"audio_url": reference},
            "images": {"images": [{"url": reference}]},
        }

        answer = await _exporter(workspace).complete(results[key])

        assert answer["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.{extension}"]

    async def test_a_repeat_neither_rewrites_nor_clobbers_it(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        exporter = _exporter(workspace)
        await exporter.complete(_image_result(record.id))
        copy = workspace / WORKSPACE_MEDIA_DIR / f"{record.id}.png"
        copy.write_bytes(b"the user edited it")

        again = await exporter.complete(_image_result(record.id))

        assert again["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.png"]
        assert copy.read_bytes() == b"the user edited it"

    async def test_concurrent_calls_end_with_one_complete_file_and_no_temporary_one(
        self, library: MediaLibraryEnv, workspace: Path
    ) -> None:
        record = await library.store(_PNG)
        exporter = _exporter(workspace)

        answers = await asyncio.gather(*(exporter.complete(_image_result(record.id)) for _ in range(6)))

        assert all("workspace_files_error" not in answer for answer in answers)
        assert sorted(entry.name for entry in (workspace / WORKSPACE_MEDIA_DIR).iterdir()) == sorted(
            [".gitignore", f"{record.id}.png"]
        )

    async def test_every_reference_of_a_result_is_copied_once_in_order(self, library: MediaLibraryEnv, workspace: Path) -> None:
        video = await library.store(_PNG, content_type="video/mp4")
        image = await library.store(_PNG)
        audio = await library.store(_PNG, content_type="audio/mpeg")
        result: dict[str, object] = {
            "video_urls": [media_reference(video.id), media_reference(video.id), "https://cdn.example/a.mp4"],
            "images": [{"url": media_reference(image.id)}, {"url": None}, "junk"],
            "audio_url": media_reference(audio.id),
        }

        answer = await _exporter(workspace).complete(result)

        assert answer["workspace_files"] == [
            f"./{WORKSPACE_MEDIA_DIR}/{video.id}.mp4",
            f"./{WORKSPACE_MEDIA_DIR}/{image.id}.png",
            f"./{WORKSPACE_MEDIA_DIR}/{audio.id}.mp3",
        ]
        assert "workspace_files_error" not in answer

    async def test_a_result_without_media_is_returned_as_it_is(self, workspace: Path) -> None:
        result: dict[str, object] = {"error": "prompt is required", "images": [{"url": "https://cdn.example/a.png"}]}

        assert await _exporter(workspace).complete(result) == result
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    async def test_the_first_copy_is_logged_and_a_repeat_is_not(
        self, library: MediaLibraryEnv, workspace: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        record = await library.store(_PNG)
        exporter = _exporter(workspace)

        with caplog.at_level(logging.INFO, logger=_LOGGER):
            await exporter.complete(_image_result(record.id))
            await exporter.complete(_image_result(record.id))

        copied = [entry for entry in caplog.records if entry.name == _LOGGER and record.id in entry.getMessage()]
        assert len(copied) == 1


class TestWhatCannotBeCopied:
    @pytest.mark.parametrize(
        "reference",
        ["media://../outside", "media://a/b", "media://a\x00b", "media://" + "x" * 65, "media://media_1.png", "media://.."],
    )
    async def test_an_id_that_could_name_another_path_is_refused(self, workspace: Path, reference: str) -> None:
        answer = await _exporter(workspace).complete({"video_urls": [reference]})

        assert str(answer["workspace_files_error"]) == f"{reference}: not a media library reference"
        assert "workspace_files" not in answer
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()
        assert list(workspace.parent.iterdir()) == [workspace]

    async def test_an_unknown_item_is_reported(self, library: MediaLibraryEnv, workspace: Path) -> None:
        answer = await _exporter(workspace).complete({"video_urls": ["media://media_unknown"]})

        assert answer["workspace_files_error"] == "media://media_unknown: it is no longer in the media library"

    async def test_a_copy_that_fails_midway_leaves_no_partial_file(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)

        def fail_after_writing_half(source: BinaryIO, destination: BinaryIO) -> None:
            destination.write(_PNG[:10])
            raise OSError(errno.EIO, "Input/output error")

        with patch("app.ai_agents.media_tools.workspace_export.shutil.copyfileobj", side_effect=fail_after_writing_half):
            answer = await _exporter(workspace).complete(_image_result(record.id))

        assert "Input/output error" in str(answer["workspace_files_error"])
        assert [entry.name for entry in (workspace / WORKSPACE_MEDIA_DIR).iterdir()] == [".gitignore"]

    async def test_a_file_missing_from_storage_is_reported(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        await library.storage.delete(record.storage_key)

        answer = await _exporter(workspace).complete(_image_result(record.id))

        assert answer["workspace_files_error"] == f"media://{record.id}: its file is missing from storage"
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    async def test_storage_that_is_not_on_the_local_disk_is_reported(
        self, library: MediaLibraryEnv, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = await library.store(_PNG)
        monkeypatch.setattr(library.storage, "local_path", lambda key: None)

        answer = await _exporter(workspace).complete(_image_result(record.id))

        assert "stored remotely" in str(answer["workspace_files_error"])
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    async def test_a_file_without_a_usable_extension_is_reported(
        self, library: MediaLibraryEnv, workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = await library.store(_PNG)
        odd = tmp_path / "odd.x y"
        odd.write_bytes(_PNG)
        monkeypatch.setattr(library.storage, "local_path", lambda key: odd)

        answer = await _exporter(workspace).complete(_image_result(record.id))

        assert "no usable extension" in str(answer["workspace_files_error"])

    async def test_a_missing_workspace_is_reported(self, library: MediaLibraryEnv, tmp_path: Path) -> None:
        record = await library.store(_PNG)

        answer = await _exporter(tmp_path / "gone").complete(_image_result(record.id))

        assert "workspace folder does not exist" in str(answer["workspace_files_error"])
        assert "workspace_files" not in answer

    @pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="permission bits do not bind this user")
    async def test_an_unwritable_workspace_is_reported(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        workspace.chmod(0o500)
        try:
            answer = await _exporter(workspace).complete(_image_result(record.id))
        finally:
            workspace.chmod(0o700)

        assert "could not be written" in str(answer["workspace_files_error"])
        assert "workspace_files" not in answer

    async def test_a_storage_key_that_leaves_the_storage_root_is_reported(
        self, library: MediaLibraryEnv, workspace: Path
    ) -> None:
        record = await library.store(_PNG)
        await library.point_at(record.id, "../outside.png")

        answer = await _exporter(workspace).complete(_image_result(record.id))

        assert answer["workspace_files_error"] == f"media://{record.id}: its storage location is invalid"
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    async def test_what_could_be_copied_is_listed_next_to_what_could_not(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG, content_type="video/mp4")

        answer = await _exporter(workspace).complete({"video_urls": [media_reference(record.id), "media://media_gone"]})

        assert answer["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.mp4"]
        assert answer["workspace_files_error"] == "media://media_gone: it is no longer in the media library"

    async def test_a_failure_nobody_foresaw_is_reported_and_logged_without_failing_the_call(
        self, library: MediaLibraryEnv, workspace: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        good = await library.store(_PNG, content_type="video/mp4")
        broken = await library.store(_PNG, content_type="video/mp4")

        async def locate(media_id: str) -> Path | None:
            if media_id == broken.id:
                raise RuntimeError("database is locked")
            return await local_media_file(media_id)

        with patch(f"{_LOGGER}.local_media_file", locate), caplog.at_level(logging.WARNING, logger=_LOGGER):
            answer = await _exporter(workspace).complete({"video_urls": [media_reference(broken.id), media_reference(good.id)]})

        assert answer["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{good.id}.mp4"]
        assert answer["workspace_files_error"] == f"media://{broken.id}: the copy failed unexpectedly"
        assert [entry for entry in caplog.records if entry.exc_info and broken.id in entry.getMessage()]

    async def test_cancelling_the_call_is_not_swallowed(self, workspace: Path) -> None:
        async def cancelled(media_id: str) -> Path | None:
            raise asyncio.CancelledError

        with patch(f"{_LOGGER}.local_media_file", cancelled), pytest.raises(asyncio.CancelledError):
            await _exporter(workspace).complete(_image_result("media_any"))


class TestAJsonAnswer:
    async def test_a_result_with_media_gets_the_copies(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_PNG)
        answer = json.dumps(_image_result(record.id))

        completed = json.loads(await _exporter(workspace).complete_json(answer))

        assert completed["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.png"]
        assert completed["model"] == "m"

    @pytest.mark.parametrize("answer", ['{"error": "image_url is required"}', "not json at all", "[1, 2]", '"text"'])
    async def test_anything_else_comes_back_as_the_same_text(self, workspace: Path, answer: str) -> None:
        assert await _exporter(workspace).complete_json(answer) is answer


class TestTheWorkspaceOfAnAgent:
    async def test_the_first_declared_folder_is_the_workspace(self, library: MediaLibraryEnv, tmp_path: Path) -> None:
        chat, shared = tmp_path / "chat-1", tmp_path / "shared"
        chat.mkdir()
        shared.mkdir()
        record = await library.store(_PNG)

        exporter = WorkspaceExporter.for_workspace([str(chat), str(shared)], user_id="u", chat_id="c")

        assert exporter is not None
        await exporter.complete(_image_result(record.id))
        assert (chat / WORKSPACE_MEDIA_DIR / f"{record.id}.png").is_file()
        assert not (shared / WORKSPACE_MEDIA_DIR).exists()

    @pytest.mark.parametrize("roots", [[], [""], ["  "]])
    def test_an_agent_without_a_workspace_has_no_exporter(self, roots: list[str]) -> None:
        assert WorkspaceExporter.for_workspace(roots) is None
