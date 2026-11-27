"""The workspace export does its file-system work off the event loop.

[INPUT]
- app.ai_agents.media_tools.workspace_export::WorkspaceExporter (POS: completes a result with the workspace copies of its media)
- tests.support.media_library_env::media_library_env (POS: a real media library on a throwaway database and local storage)

[OUTPUT]
Fails when locating the stored file, creating the copies' folder or copying the bytes blocks the event loop; the
blockbuster gate that wraps every test in this directory reports blocking calls made under ``app.*`` frames.

[POS]
tests/blocking_io/test_media_workspace_export.py; a status poll runs on the loop that serves every other chat, so one
slow disk must not stall it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai_agents.media_tools.workspace_export import WORKSPACE_MEDIA_DIR, WorkspaceExporter
from app.core.media.reference import media_reference
from tests.support.media_library_env import media_library_env

_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))


async def test_exporting_media_never_blocks_the_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    exporter = WorkspaceExporter(workspace, user_id="user-1", chat_id="chat-1")

    async with media_library_env(tmp_path / "storage", monkeypatch) as library:
        record = await library.store(_PNG)
        result = {"images": [{"url": media_reference(record.id)}]}

        first = await exporter.complete(result)
        # The repeat takes the path of an existing copy; a media item that is gone takes the error path.
        repeat = await exporter.complete(result)
        gone = await exporter.complete({"images": [{"url": media_reference("media_missing")}]})

    expected = [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.png"]
    assert first["workspace_files"] == expected
    assert repeat["workspace_files"] == expected
    assert "no longer in the media library" in str(gone["workspace_files_error"])
