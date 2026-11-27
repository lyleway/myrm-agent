"""The image and video tools put the media they produce into the workspace: a finished task's status and an edit's answer.

[INPUT]
- app.ai_agents.media_tools.image_agent_tool / video_agent_tool / tts_agent_tool (POS: the tools whose results gain ``workspace_files``)
- app.ai_agents.media_tools.workspace_export::WorkspaceExporter (POS: the gate that decides whose task is exported)
- tests.support.media_library_env::media_library_env (POS: a real media library on a throwaway database and local storage)
- myrm_agent_harness.core.security.detection::redact_leaks (POS: the credential redaction the agent stack applies to every tool result)

[OUTPUT]
Fails when a result lacks the copies of its media, when the status of a task of another user or chat writes into the
workspace, when a failed copy changes the result it completes, when an agent without a workspace gets another answer,
when what the model sees of a tool depends on the workspace, the user or the chat, or when the credential redaction masks
a path the answer names.

[POS]
tests/unit/ai_agents/media_tools/test_workspace_export_wiring.py; the model call and the task queue are replaced, the media
library, the reference resolver, the exporter and the credential redaction are real.
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from myrm_agent_harness.core.security.detection import redact_leaks
from myrm_agent_harness.toolkits.llms.image.models import ImageGenerationConfig
from myrm_agent_harness.toolkits.llms.tts import TTSConfig
from myrm_agent_harness.toolkits.llms.video import VideoGenerationConfig
from myrm_agent_harness.toolkits.tasks import Task, TaskStatus
from PIL import Image

from app.ai_agents.media_tools.image_agent_tool import create_image_generation_tool
from app.ai_agents.media_tools.tts_agent_tool import create_tts_tool
from app.ai_agents.media_tools.video_agent_tool import create_video_generation_tool
from app.ai_agents.media_tools.workspace_export import WORKSPACE_MEDIA_DIR, WorkspaceExporter
from app.core.media.reference import media_reference
from app.tasks.serializer import serialize_media_task
from tests.support.media_library_env import MediaLibraryEnv, media_library_env

_RESULTS = "app.ai_agents.media_tools.task_results"
_IMAGE_MODULE = "app.ai_agents.media_tools.image_agent_tool"
_TTS_CONFIG = TTSConfig(provider="elevenlabs", model="eleven_flash_v2_5", voice="21m00Tcm4TlvDq8ikWAM", api_key="test-key")
_BYTES = b"the generated media"


@pytest.fixture
async def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MediaLibraryEnv]:
    async with media_library_env(tmp_path / "storage", monkeypatch) as env:
        yield env


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _roots(workspace: Path | None) -> list[str]:
    return [] if workspace is None else [str(workspace)]


def _image_tool(workspace: Path | None, *, user_id: str = "user-1", chat_id: str | None = "chat-1") -> BaseTool:
    return create_image_generation_tool(
        ImageGenerationConfig(model="gpt-image-2.6-sunburst"),
        allowed_roots=_roots(workspace),
        task_user_id=user_id,
        chat_id=chat_id,
    )


def _video_tool(workspace: Path | None, *, user_id: str = "user-1", chat_id: str | None = "chat-1") -> BaseTool:
    return create_video_generation_tool(
        VideoGenerationConfig(provider="volcengine", model="seedance-2-0"),
        allowed_roots=_roots(workspace),
        task_user_id=user_id,
        chat_id=chat_id,
    )


def _tts_tool(workspace: Path | None, *, user_id: str = "user-1", chat_id: str | None = "chat-1") -> BaseTool:
    """Speech is synthesized inside the call, so the tool knows no task owner and ignores ``user_id`` and ``chat_id``."""
    return create_tts_tool(_TTS_CONFIG, allowed_roots=_roots(workspace))


@dataclass(frozen=True)
class _Kind:
    """A queue-backed tool and the shape of the result its tasks end with."""

    make_tool: Callable[..., BaseTool]
    task_type: str
    content_type: str
    extension: str
    result: Callable[[str], dict[str, object]]


_IMAGE = _Kind(
    _image_tool,
    "image_generate",
    "image/png",
    "png",
    lambda ref: {"images": [{"url": ref, "mime_type": "image/png"}], "model": "m", "prompt": "p", "latency_ms": 1},
)
_VIDEO = _Kind(
    _video_tool, "video_generate", "video/mp4", "mp4", lambda ref: {"video_urls": [ref], "provider": "p", "model": "m"}
)
_KINDS = pytest.mark.parametrize("kind", [_IMAGE, _VIDEO], ids=["image_tool", "video_tool"])


def _task(
    kind: _Kind,
    result: dict[str, object] | None,
    *,
    user_id: str = "user-1",
    chat_id: str | None = "chat-1",
    status: TaskStatus | None = None,
) -> Task:
    """A task of ``kind``: succeeded with ``result``, or still running without one, unless ``status`` says otherwise."""
    state = status or (TaskStatus.SUCCEEDED if result is not None else TaskStatus.RUNNING)
    payload: dict[str, object] = {"chat_id": chat_id} if chat_id else {}
    return Task(task_id="t1", task_type=kind.task_type, user_id=user_id, status=state, payload=payload, result=result)


async def _status(tool: BaseTool, *tasks: Task, **arguments: object) -> dict[str, object]:
    """What ``tool`` answers to a status call that reads ``tasks`` from the queue, one per read."""
    store = MagicMock()
    store.get_task = AsyncMock(side_effect=list(tasks))
    with patch(f"{_RESULTS}.media_task_store", return_value=store):
        return json.loads(await tool.ainvoke({"action": "status", "task_id": "t1", **arguments}))


class TestWhoseTaskIsExported:
    @pytest.mark.parametrize(
        ("exporter_chat", "task_user", "task_chat", "exported"),
        [
            ("chat-1", "user-1", "chat-1", True),
            ("chat-1", "user-2", "chat-1", False),
            ("chat-1", "user-1", "chat-2", False),
            ("chat-1", "user-1", None, False),
            (None, "user-1", "chat-1", False),
            (None, "user-1", None, True),
            ("", "user-1", None, True),
        ],
        ids=["own", "other user", "other chat", "task without chat", "exporter without chat", "neither has a chat", "blank chat"],
    )
    async def test_only_a_task_of_this_user_and_chat_is_exported(
        self,
        library: MediaLibraryEnv,
        workspace: Path,
        exporter_chat: str | None,
        task_user: str,
        task_chat: str | None,
        exported: bool,
    ) -> None:
        record = await library.store(_BYTES, content_type="video/mp4")
        task = _task(_VIDEO, _VIDEO.result(media_reference(record.id)), user_id=task_user, chat_id=task_chat)
        status = serialize_media_task(task)

        answer = await WorkspaceExporter(workspace, user_id="user-1", chat_id=exporter_chat).complete_status(task, status)

        if exported:
            assert answer["result"] == {**(task.result or {}), "workspace_files": [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.mp4"]}
        else:
            assert answer == status
            assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    @pytest.mark.parametrize("status", [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED])
    async def test_a_task_that_has_not_succeeded_is_not_exported(
        self, library: MediaLibraryEnv, workspace: Path, status: TaskStatus
    ) -> None:
        record = await library.store(_BYTES, content_type="video/mp4")
        task = _task(_VIDEO, _VIDEO.result(media_reference(record.id)), status=status)
        serialized = serialize_media_task(task)

        assert (
            await WorkspaceExporter(workspace, user_id="user-1", chat_id="chat-1").complete_status(task, serialized) == serialized
        )
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    async def test_the_task_record_itself_is_not_changed(self, library: MediaLibraryEnv, workspace: Path) -> None:
        record = await library.store(_BYTES, content_type="video/mp4")
        result = _VIDEO.result(media_reference(record.id))
        task = _task(_VIDEO, result)

        await WorkspaceExporter(workspace, user_id="user-1", chat_id="chat-1").complete_status(task, serialize_media_task(task))

        assert task.result == _VIDEO.result(media_reference(record.id))

    async def test_a_succeeded_task_without_a_result_is_returned_as_it_is(self, workspace: Path) -> None:
        task = Task(task_id="t1", task_type="video_generate", user_id="user-1", status=TaskStatus.SUCCEEDED, payload={})
        status = serialize_media_task(task)

        assert await WorkspaceExporter(workspace, user_id="user-1", chat_id=None).complete_status(task, status) == status


class TestStatus:
    @_KINDS
    async def test_a_finished_task_of_this_chat_names_the_copies_of_its_media(
        self, library: MediaLibraryEnv, workspace: Path, kind: _Kind
    ) -> None:
        record = await library.store(_BYTES, content_type=kind.content_type)
        result = kind.result(media_reference(record.id))

        answer = await _status(kind.make_tool(workspace), _task(kind, result))

        assert answer["status"] == "succeeded"
        assert answer["result"] == {**result, "workspace_files": [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.{kind.extension}"]}
        assert (workspace / WORKSPACE_MEDIA_DIR / f"{record.id}.{kind.extension}").read_bytes() == _BYTES

    @_KINDS
    async def test_waiting_for_the_task_ends_with_the_copies_of_what_it_made(
        self, library: MediaLibraryEnv, workspace: Path, kind: _Kind
    ) -> None:
        record = await library.store(_BYTES, content_type=kind.content_type)
        result = kind.result(media_reference(record.id))

        with patch(f"{_RESULTS}.sleep", AsyncMock()):
            answer = await _status(kind.make_tool(workspace), _task(kind, None), _task(kind, result), wait_seconds=5)

        assert answer["status"] == "succeeded"
        assert answer["result"]["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.{kind.extension}"]

    @_KINDS
    @pytest.mark.parametrize(("task_user", "task_chat"), [("user-2", "chat-1"), ("user-1", "chat-2"), ("user-1", None)])
    async def test_a_task_of_another_user_or_chat_is_answered_without_a_copy(
        self, library: MediaLibraryEnv, workspace: Path, kind: _Kind, task_user: str, task_chat: str | None
    ) -> None:
        record = await library.store(_BYTES, content_type=kind.content_type)
        result = kind.result(media_reference(record.id))

        answer = await _status(kind.make_tool(workspace), _task(kind, result, user_id=task_user, chat_id=task_chat))

        assert answer["result"] == result
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    @_KINDS
    async def test_a_task_that_is_still_running_is_answered_as_before(self, workspace: Path, kind: _Kind) -> None:
        answer = await _status(kind.make_tool(workspace), _task(kind, None))

        assert answer["status"] == "running"
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    @_KINDS
    async def test_an_agent_without_a_workspace_gets_the_result_as_it_is(
        self, library: MediaLibraryEnv, workspace: Path, kind: _Kind
    ) -> None:
        record = await library.store(_BYTES, content_type=kind.content_type)
        result = kind.result(media_reference(record.id))

        answer = await _status(kind.make_tool(None), _task(kind, result))

        assert answer["result"] == result
        assert list(workspace.iterdir()) == []

    @_KINDS
    async def test_a_copy_that_fails_leaves_the_result_as_it_is_and_says_so(self, workspace: Path, kind: _Kind) -> None:
        result = kind.result(media_reference("media_gone"))

        answer = await _status(kind.make_tool(workspace), _task(kind, result))

        assert answer["result"] == {**result, "workspace_files_error": "media://media_gone: it is no longer in the media library"}


class TestEdit:
    @staticmethod
    def _engine_answer(media_id: str) -> str:
        return json.dumps(
            {
                "images": [{"url": media_reference(media_id), "mime_type": "image/png"}],
                "model": "m",
                "prompt": "bluer",
                "latency_ms": 5,
            }
        )

    async def _edit(self, workspace: Path | None, source_id: str, engine_answer: str) -> str:
        editor = MagicMock()
        editor.edit_image = AsyncMock(return_value=engine_answer)
        with patch(f"{_IMAGE_MODULE}.ImageGenerationTools", return_value=editor):
            arguments = {"action": "edit", "prompt": "bluer", "image_url": media_reference(source_id)}
            return str(await _image_tool(workspace).ainvoke(arguments))

    async def test_the_answer_names_the_copy_of_the_edited_image(self, library: MediaLibraryEnv, workspace: Path) -> None:
        source, edited = await library.store(_png()), await library.store(_BYTES)

        answer = json.loads(await self._edit(workspace, source.id, self._engine_answer(edited.id)))

        assert answer["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{edited.id}.png"]
        assert answer["images"][0]["url"] == media_reference(edited.id)
        assert (workspace / WORKSPACE_MEDIA_DIR / f"{edited.id}.png").read_bytes() == _BYTES

    async def test_an_agent_without_a_workspace_gets_what_the_engine_answered(
        self, library: MediaLibraryEnv, workspace: Path
    ) -> None:
        source, edited = await library.store(_png()), await library.store(_BYTES)
        engine_answer = self._engine_answer(edited.id)

        assert await self._edit(None, source.id, engine_answer) == engine_answer
        assert list(workspace.iterdir()) == []

    async def test_an_error_of_the_engine_is_returned_untouched(self, library: MediaLibraryEnv, workspace: Path) -> None:
        source = await library.store(_png())
        engine_answer = '{"error": "the provider rejected the image"}'

        assert await self._edit(workspace, source.id, engine_answer) == engine_answer
        assert not (workspace / WORKSPACE_MEDIA_DIR).exists()

    async def test_a_copy_that_fails_leaves_the_edit_as_it_is_and_says_so(
        self, library: MediaLibraryEnv, workspace: Path
    ) -> None:
        source = await library.store(_png())
        engine_answer = self._engine_answer("media_gone")

        answer = json.loads(await self._edit(workspace, source.id, engine_answer))

        assert answer == {
            **json.loads(engine_answer),
            "workspace_files_error": "media://media_gone: it is no longer in the media library",
        }


def _as_the_model_sees_it(tool: BaseTool) -> str:
    return json.dumps(convert_to_openai_tool(tool), ensure_ascii=False)


_EVERY_TOOL = pytest.mark.parametrize(
    "make_tool", [_image_tool, _video_tool, _tts_tool], ids=["image_tool", "video_tool", "tts_generate"]
)


class TestWhatTheModelSees:
    @_EVERY_TOOL
    def test_each_tool_says_where_the_copies_are_named_and_what_their_absence_means(
        self, make_tool: Callable[..., BaseTool], workspace: Path
    ) -> None:
        description = make_tool(workspace).description

        assert "workspace_files" in description
        assert "without that field there is no copy" in description

    async def test_the_copy_paths_are_not_masked_as_secrets_before_the_model_reads_them(
        self, library: MediaLibraryEnv, workspace: Path
    ) -> None:
        """A masked path opens no file. The agent stack masks random-looking tokens and media ids are random, so the
        bare ``_generated/<id>.png`` form is masked in most results; forty ids leave no chance of passing by luck."""
        records = [await library.store(_BYTES + bytes([index])) for index in range(40)]
        result = {"images": [{"url": media_reference(record.id)} for record in records]}

        answer = await WorkspaceExporter(workspace, user_id="user-1", chat_id="chat-1").complete(result)

        text = json.dumps(answer)
        assert text.count(WORKSPACE_MEDIA_DIR) == len(records)
        assert redact_leaks(text) == text

    @_EVERY_TOOL
    def test_the_tool_as_sent_to_the_model_does_not_depend_on_the_workspace_user_or_chat(
        self, make_tool: Callable[..., BaseTool], tmp_path: Path
    ) -> None:
        first, second = tmp_path / "chat-1", tmp_path / "chat-2"

        sent = {
            _as_the_model_sees_it(make_tool(None)),
            _as_the_model_sees_it(make_tool(first)),
            _as_the_model_sees_it(make_tool(second, user_id="user-2", chat_id="chat-2")),
            _as_the_model_sees_it(make_tool(second, user_id="user-3", chat_id=None)),
        }

        assert len(sent) == 1
