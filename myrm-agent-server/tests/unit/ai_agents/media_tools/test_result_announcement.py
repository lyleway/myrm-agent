"""A media tool announces what a call produced to the chat stream, and the stream collector registers the card from it.

[INPUT]
- app.ai_agents.media_tools.result_announcement::announce_result (POS: pushes a result into the chat stream)
- app.ai_agents.media_tools.image_agent_tool / video_agent_tool / tts_agent_tool (POS: the tools whose results the message shows cards for)
- app.services.agent.streaming_support.stream_collector::StreamContentCollector (POS: registers the cards of a message from the events of its stream)
- myrm_agent_harness.utils.runtime.progress_sink::set_tool_progress_sink (POS: the channel through which a running tool reaches the stream)
- tests.unit.ai_agents.media_tools.conftest::library (POS: a real media library on a throwaway database and local storage)

[OUTPUT]
Fails when a call that produced media puts nothing into the stream, announces another text than the one the model reads or
another tool name than its own, when a call that produced nothing announces something, when a stream that cannot be told
fails the call, or when the collector registers no card from what a tool announced.

[POS]
tests/unit/ai_agents/media_tools/test_result_announcement.py; the engines and the task queue are replaced, the tools, the
channel contract of the harness and the stream collector are real. The stream carries no tool result by itself, so these
announcements are the only way a message learns of the media its tools made.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.tools import BaseTool
from myrm_agent_harness.toolkits.llms.image.models import ImageGenerationConfig
from myrm_agent_harness.toolkits.llms.tts import TTSConfig, TTSGenerationError, TTSResult
from myrm_agent_harness.toolkits.llms.video import VideoGenerationConfig
from myrm_agent_harness.utils.runtime.progress_sink import set_tool_progress_sink
from PIL import Image
from pydantic import SecretStr

from app.ai_agents.media_tools.image_agent_tool import create_image_generation_tool
from app.ai_agents.media_tools.result_announcement import announce_result
from app.ai_agents.media_tools.tts_agent_tool import create_tts_tool
from app.ai_agents.media_tools.video_agent_tool import create_video_generation_tool
from app.core.media.reference import media_reference
from app.services.agent.streaming_support.stream_collector import StreamContentCollector
from tests.support.media_library_env import MediaLibraryEnv

_IMAGE_MODULE = "app.ai_agents.media_tools.image_agent_tool"
_VIDEO_MODULE = "app.ai_agents.media_tools.video_agent_tool"
_RESULTS = "app.ai_agents.media_tools.task_results"
_TTS_ENGINE = "app.ai_agents.media_tools.tts_agent_tool.AsyncTTSEngine.generate"
_ANNOUNCER = "app.ai_agents.media_tools.result_announcement"
_IMAGE_MODEL = "gpt-image-2.6-sunburst"
_TTS_CONFIG = TTSConfig(
    provider="elevenlabs", model="eleven_flash_v2_5", voice="21m00Tcm4TlvDq8ikWAM", api_key=SecretStr("test-key")
)


class _Stream:
    """The channel of a running chat stream: it records what a tool pushes into it."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def emit(self, event: dict[str, object]) -> None:
        self.events.append(event)


class _ClosedStream:
    async def emit(self, event: dict[str, object]) -> None:
        raise RuntimeError("the stream is closed")


@pytest.fixture
def stream() -> Iterator[_Stream]:
    channel = _Stream()
    set_tool_progress_sink(channel)
    yield channel
    set_tool_progress_sink(None)


def _registered(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """The cards the stream collector registers from the events a stream carried."""
    collector = StreamContentCollector()
    for event in events:
        collector.feed_event(event)
    registered = (collector.extra_data or {}).get("media_generated", [])
    assert isinstance(registered, list)
    return registered


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_tool() -> BaseTool:
    return create_image_generation_tool(ImageGenerationConfig(model=_IMAGE_MODEL))


def _video_tool() -> BaseTool:
    return create_video_generation_tool(VideoGenerationConfig(provider="volcengine", model="seedance-2-0"))


def _speech(*, persisted_ref: str | None = "media://media_speech") -> TTSResult:
    return TTSResult(
        audio_bytes=b"audio",
        mime_type="audio/mpeg",
        provider="elevenlabs",
        model="eleven_flash_v2_5",
        latency_ms=150.4,
        persisted_ref=persisted_ref,
        duration_seconds=3.24,
    )


async def _edit_image(library: MediaLibraryEnv, engine_answer: str) -> tuple[BaseTool, str]:
    source = await library.store(_png(), content_type="image/png")
    editor = MagicMock()
    editor.edit_image = AsyncMock(return_value=engine_answer)
    with patch(f"{_IMAGE_MODULE}.ImageGenerationTools", return_value=editor):
        tool = _image_tool()
        arguments = {"action": "edit", "prompt": "bluer", "image_url": media_reference(source.id)}
        return tool, str(await tool.ainvoke(arguments))


class TestAnnounceResult:
    async def test_the_result_goes_into_the_stream_as_the_tool_end_of_the_tool(self, stream: _Stream) -> None:
        assert await announce_result("image_tool", '{"tasks": []}') == '{"tasks": []}'

        assert stream.events == [{"type": "tool_end", "tool_name": "image_tool", "result": '{"tasks": []}'}]

    async def test_a_call_outside_a_chat_stream_announces_nothing_and_returns_the_result(self) -> None:
        assert await announce_result("image_tool", "answer") == "answer"

    async def test_a_stream_that_cannot_be_told_never_fails_the_call(self, caplog: pytest.LogCaptureFixture) -> None:
        set_tool_progress_sink(_ClosedStream())
        try:
            with caplog.at_level(logging.WARNING, logger=_ANNOUNCER):
                assert await announce_result("video_tool", "answer") == "answer"
        finally:
            set_tool_progress_sink(None)

        assert [record.name for record in caplog.records] == [_ANNOUNCER]
        assert "video_tool" in caplog.records[0].getMessage()


class TestTheToolsAnnounceWhatTheyProduce:
    async def test_image_generate_announces_the_tasks_it_started(self, stream: _Stream) -> None:
        enqueuer = MagicMock()
        enqueuer.generate_image = AsyncMock(side_effect=["img-1", "img-2"])
        tool = _image_tool()

        with (
            patch(f"{_IMAGE_MODULE}.AsyncImageGenerationTools", return_value=enqueuer),
            patch(f"{_IMAGE_MODULE}.media_task_store", return_value=MagicMock()),
        ):
            answer = await tool.ainvoke({"action": "generate", "prompt": "a cat", "n": 2})

        assert stream.events == [{"type": "tool_end", "tool_name": tool.name, "result": answer}]
        assert _registered(stream.events) == [{"kind": "image", "task_id": "img-1"}, {"kind": "image", "task_id": "img-2"}]

    async def test_image_edit_announces_the_edited_image(self, library: MediaLibraryEnv, stream: _Stream) -> None:
        engine_answer = json.dumps({"images": [{"url": "media://media_edit", "mime_type": "image/png"}], "model": _IMAGE_MODEL})

        tool, answer = await _edit_image(library, engine_answer)

        assert stream.events == [{"type": "tool_end", "tool_name": tool.name, "result": answer}]
        assert _registered(stream.events) == [{"kind": "image", "media": "media://media_edit", "model": _IMAGE_MODEL}]

    async def test_video_generate_announces_the_task_it_started(self, stream: _Stream) -> None:
        enqueuer = MagicMock()
        enqueuer.generate_video = AsyncMock(return_value="vid-1")
        tool = _video_tool()

        with (
            patch(f"{_VIDEO_MODULE}.AsyncVideoGenerationTools", return_value=enqueuer),
            patch(f"{_VIDEO_MODULE}.media_task_store", return_value=MagicMock()),
        ):
            answer = await tool.ainvoke({"action": "generate", "prompt": "a dog running"})

        assert stream.events == [{"type": "tool_end", "tool_name": tool.name, "result": answer}]
        assert _registered(stream.events) == [{"kind": "video", "task_id": "vid-1"}]

    async def test_speech_announces_the_audio_it_stored(self, stream: _Stream) -> None:
        tool = create_tts_tool(_TTS_CONFIG)

        with patch(_TTS_ENGINE, new_callable=AsyncMock, return_value=_speech()):
            answer = await tool.ainvoke({"text": "Hello"})

        assert stream.events == [{"type": "tool_end", "tool_name": tool.name, "result": answer}]
        assert _registered(stream.events) == [
            {"kind": "tts", "media": "media://media_speech", "model": "eleven_flash_v2_5", "duration_seconds": 3.2}
        ]


class TestACallThatProducedNothingAnnouncesNothing:
    async def test_listing_the_models_announces_nothing(self, stream: _Stream) -> None:
        await _image_tool().ainvoke({"action": "list"})
        await _video_tool().ainvoke({"action": "list"})

        assert stream.events == []

    async def test_a_status_call_announces_nothing(self, stream: _Stream) -> None:
        store = MagicMock()
        store.get_task = AsyncMock(return_value=None)

        with patch(f"{_RESULTS}.media_task_store", return_value=store):
            await _image_tool().ainvoke({"action": "status", "task_id": "t1"})
            await _video_tool().ainvoke({"action": "status", "task_id": "t1"})

        assert stream.events == []

    async def test_a_rejected_request_announces_nothing(self, stream: _Stream) -> None:
        await _image_tool().ainvoke({"action": "generate", "prompt": "  "})
        await _video_tool().ainvoke({"action": "generate", "prompt": "  "})
        await create_tts_tool(_TTS_CONFIG).ainvoke({"text": "  "})

        assert stream.events == []

    @pytest.mark.parametrize(
        "failure",
        [TTSGenerationError("provider down"), None],
        ids=["engine failure", "audio not stored"],
    )
    async def test_speech_that_failed_announces_nothing(self, stream: _Stream, failure: TTSGenerationError | None) -> None:
        engine = AsyncMock(side_effect=failure) if failure else AsyncMock(return_value=_speech(persisted_ref=None))

        with patch(_TTS_ENGINE, engine):
            await create_tts_tool(_TTS_CONFIG).ainvoke({"text": "Hello"})

        assert stream.events == []

    async def test_an_edit_the_provider_rejected_registers_no_card(self, library: MediaLibraryEnv, stream: _Stream) -> None:
        await _edit_image(library, '{"error": "the provider rejected the image"}')

        assert _registered(stream.events) == []
