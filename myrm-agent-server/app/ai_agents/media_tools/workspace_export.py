"""Copies the media a tool result names into the agent's workspace, so that bash and file tools can open the files.

[INPUT]
- app.core.media.reference::local_media_file, parse_media_reference (POS: ``media://<id>`` -> the stored file on the local disk)
- myrm_agent_harness.core.security.path::open_directory_beneath, ConfinedDirectory (POS: exclusive file creation below a trusted directory, decided on descriptors so no link redirects it)
- myrm_agent_harness.toolkits.tasks::Task, TaskStatus (POS: the queue task a status answer describes)
- myrm_agent_harness.toolkits.storage.base::StorageError (POS: a storage key that escapes the storage root)

[OUTPUT]
- WORKSPACE_MEDIA_DIR: the folder of the copies inside the workspace
- WorkspaceExporter: completes a result with ``workspace_files`` (``./_generated/<id>.<ext>``: the paths of the copies,
  relative to the workspace root) and ``workspace_files_error`` (why a copy is missing)

[POS]
The output counterpart of ``_uploaded/``: inputs reach the workspace as files, generated media would otherwise exist only
as ``media://`` references that code tools cannot open. A copy (never a link) is made on demand, so editing it in place
cannot reach the media library's original, and a ``.gitignore`` keeps the copies out of the user's repository. The
server writes into a folder the agent's sandbox can also write, so the folder is opened once without following a link,
and the folder's ``.gitignore`` and every temporary file are created through that open folder: swapping its name for a
link afterwards redirects none of them. Names are looked up again only to see whether a copy exists, to publish a copy
by rename and to remove a folder left empty after a failure; a swapped folder holds no temporary file for the rename,
so nothing is published.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import uuid4

from myrm_agent_harness.core.security.path import ConfinedDirectory, open_directory_beneath
from myrm_agent_harness.toolkits.storage.base import StorageError
from myrm_agent_harness.toolkits.tasks import Task, TaskStatus

from app.core.media.reference import local_media_file, parse_media_reference

logger = logging.getLogger(__name__)

WORKSPACE_MEDIA_DIR = "_generated"

# A file name is made of these two parts only, so a reference can never name a path outside the folder.
_MEDIA_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}")
# Kanban runs commit the whole workspace with ``git add -A``; the copies are working material, not deliverables.
_GITIGNORE = ".gitignore"
_IGNORE_EVERYTHING = b"*\n"


class _ExportError(Exception):
    """A media item that could not be copied; the message says why, in words meant for the model."""


def _open_folder(workspace_root: Path) -> ConfinedDirectory:
    """Open the folder of the copies, creating it with its git-ignore rule when it is absent; the caller closes it.

    A link in place of the folder is refused. A folder that exists is left alone, and one that could not be given
    its ignore rule is removed again unless something else lives in it.
    """
    try:
        return open_directory_beneath(workspace_root, (WORKSPACE_MEDIA_DIR,), create=False)
    except FileNotFoundError:
        pass  # absent: created below, unless a concurrent call gets there first
    folder = open_directory_beneath(workspace_root, (WORKSPACE_MEDIA_DIR,))
    try:
        _write_ignore_rule(folder)
    except OSError:
        folder.close()
        # Not recursive: opening the folder cannot tell whether this call created it, and a concurrent call may have
        # put its copies there already.
        with contextlib.suppress(OSError):
            os.rmdir(folder.path)
        raise
    return folder


def _write_ignore_rule(folder: ConfinedDirectory) -> None:
    """Give ``folder`` its ``.gitignore``.

    A rule that a concurrent call wrote first stays. One that this call created and could not finish is removed.
    """
    try:
        descriptor = folder.create_new(_GITIGNORE)
    except FileExistsError:
        return
    try:
        with os.fdopen(descriptor, "wb") as rule:
            rule.write(_IGNORE_EVERYTHING)
    except OSError:
        with contextlib.suppress(OSError):
            folder.unlink(_GITIGNORE)
        raise


def _copy_into_workspace(source: Path, workspace_root: Path, file_name: str) -> int | None:
    """Copy ``source`` to ``<workspace_root>/_generated/<file_name>``; its size, or ``None`` when that file exists already.

    The bytes go to a unique temporary name in the same folder first: a crash leaves no partial file under the real name
    and two concurrent calls end with one complete file.

    Raises:
        OSError: The workspace folder is missing or not writable, a link stands in place of the copies' folder, or the
            copy failed.
    """
    if not workspace_root.is_dir():
        raise NotADirectoryError(errno.ENOTDIR, "the workspace folder does not exist")
    with _open_folder(workspace_root.resolve()) as folder:
        destination = folder.path / file_name
        if destination.exists():
            return None
        temporary = f".{file_name}.{uuid4().hex}.part"
        try:
            with os.fdopen(folder.create_new(temporary), "wb") as writer, source.open("rb") as reader:
                shutil.copyfileobj(reader, writer)
                size = writer.tell()
            # Names resolve here, so a folder swapped for a link meanwhile finds no temporary file and publishes nothing.
            os.replace(folder.path / temporary, destination)
        finally:
            with contextlib.suppress(FileNotFoundError):
                folder.unlink(temporary)
    return size


def _media_references(result: Mapping[str, object]) -> list[str]:
    """The ``media://`` references a result names (``video_urls``, ``images[].url``, ``audio_url``), each once, in order."""
    urls: list[object] = []
    videos = result.get("video_urls")
    if isinstance(videos, list):
        urls.extend(videos)
    images = result.get("images")
    if isinstance(images, list):
        urls.extend(image.get("url") for image in images if isinstance(image, dict))
    urls.append(result.get("audio_url"))
    return list(dict.fromkeys(url for url in urls if isinstance(url, str) and parse_media_reference(url) is not None))


class WorkspaceExporter:
    """Completes tool results with copies of their media inside one chat's workspace."""

    def __init__(self, workspace_root: Path, *, user_id: str, chat_id: str | None) -> None:
        self._root = workspace_root
        self._user_id = user_id
        self._chat_id = chat_id or None

    @classmethod
    def for_workspace(
        cls,
        allowed_roots: Sequence[str],
        *,
        user_id: str = "default",
        chat_id: str | None = None,
    ) -> WorkspaceExporter | None:
        """The exporter of the workspace an agent declares (its first root), ``None`` for an agent without one."""
        root = allowed_roots[0].strip() if allowed_roots else ""
        return cls(Path(root), user_id=user_id, chat_id=chat_id) if root else None

    async def complete(self, result: Mapping[str, object]) -> dict[str, object]:
        """``result`` plus ``workspace_files`` for the media it names and ``workspace_files_error`` for what could not be copied."""
        completed = dict(result)
        files: list[str] = []
        problems: list[str] = []
        for reference in _media_references(result):
            try:
                files.append(await self._export(reference))
            except _ExportError as exc:
                problems.append(f"{reference}: {exc}")
            except Exception:
                # The media exists already, and an edit has been paid for: a failure nobody foresaw in this extra step
                # (database, storage, executor) must not turn the call that produced it into an error.
                logger.warning("Exporting generated media %s into the workspace failed", reference, exc_info=True)
                problems.append(f"{reference}: the copy failed unexpectedly")
        if files:
            completed["workspace_files"] = files
        if problems:
            completed["workspace_files_error"] = "; ".join(problems)
        return completed

    async def complete_json(self, answer: str) -> str:
        """``answer`` (a JSON result) completed by ``complete``; the text itself when there is nothing to add."""
        try:
            result = json.loads(answer)
        except ValueError:
            return answer
        if not isinstance(result, dict):
            return answer
        completed = await self.complete(result)
        return answer if completed == result else json.dumps(completed, ensure_ascii=False)

    async def complete_status(self, task: Task, status: dict[str, object]) -> dict[str, object]:
        """The status answer of ``task``; a succeeded task of this user and chat gets its result completed.

        Anyone who holds a task id can ask for its status, so a task of another chat never writes into this workspace.
        """
        result = status.get("result")
        if task.status != TaskStatus.SUCCEEDED or not isinstance(result, dict) or not self._owns(task):
            return status
        return {**status, "result": await self.complete(result)}

    def _owns(self, task: Task) -> bool:
        return task.user_id == self._user_id and task.payload.get("chat_id") == self._chat_id

    async def _export(self, reference: str) -> str:
        """The path of the copy of ``reference``, relative to the workspace root; the copy is made now unless it exists."""
        media_id = parse_media_reference(reference)
        if media_id is None or not _MEDIA_ID.fullmatch(media_id):
            raise _ExportError("not a media library reference")
        try:
            source = await local_media_file(media_id)
        except LookupError:
            raise _ExportError("it is no longer in the media library") from None
        except FileNotFoundError:
            raise _ExportError("its file is missing from storage") from None
        except StorageError:
            raise _ExportError("its storage location is invalid") from None
        if source is None:
            raise _ExportError("the media library is stored remotely, so there is no local file to copy")
        if not _EXTENSION.fullmatch(source.suffix):
            raise _ExportError("its file has no usable extension")
        file_name = f"{media_id}{source.suffix}"
        try:
            size = await asyncio.to_thread(_copy_into_workspace, source, self._root, file_name)
        except OSError as exc:
            reason = exc.strerror or str(exc) or type(exc).__name__
            raise _ExportError(f"the copy could not be written ({reason})") from None
        if size is not None:
            logger.info("Copied generated media %s into the workspace (%d bytes)", media_id, size)
        # The credential redaction of the agent stack masks random-looking tokens in tool results. It takes the bare
        # ``_generated/media_<random id>.png`` for one in most results, but strips ``./_generated/...`` as a path first.
        return f"./{WORKSPACE_MEDIA_DIR}/{file_name}"
