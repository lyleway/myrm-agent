"""Tests for the tts_generate tool: success carries the stored reference and its copy in the workspace, everything else is an explicit error."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from myrm_agent_harness.toolkits.llms.tts import TTSConfig, TTSGenerationError, TTSResult

from app.ai_agents.media_tools.tts_agent_tool import MAX_TTS_TEXT_LENGTH, TTSTool, create_tts_tool
from app.ai_agents.media_tools.workspace_export import WORKSPACE_MEDIA_DIR
from app.core.media.reference import media_reference
from tests.support.media_library_env import MediaLibraryEnv, media_library_env

_AUDIO = b"the spoken audio"


@pytest.fixture
def config() -> TTSConfig:
    return TTSConfig(provider="elevenlabs", model="eleven_flash_v2_5", voice="21m00Tcm4TlvDq8ikWAM", api_key="test-key")


@pytest.fixture
def engine_generate():
    with patch(
        "app.ai_agents.media_tools.tts_agent_tool.AsyncTTSEngine.generate",
        new_callable=AsyncMock,
    ) as mock:
        yield mock


@pytest.fixture
async def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MediaLibraryEnv]:
    async with media_library_env(tmp_path / "storage", monkeypatch) as env:
        yield env


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def _result(*, persisted_ref: str | None = "media://media_speech", duration_seconds: float | None = 3.24) -> TTSResult:
    return TTSResult(
        audio_bytes=b"audio",
        mime_type="audio/mpeg",
        provider="elevenlabs",
        model="eleven_flash_v2_5",
        latency_ms=150.4,
        persisted_ref=persisted_ref,
        duration_seconds=duration_seconds,
    )


def test_factory_returns_the_named_tool(config: TTSConfig) -> None:
    tool = create_tts_tool(config)

    assert isinstance(tool, TTSTool)
    assert tool.name == "tts_generate"
    assert tool.config == config


def test_the_tool_is_async_only(config: TTSConfig) -> None:
    with pytest.raises(NotImplementedError, match="only supports async"):
        TTSTool(config)._run(text="hello")


@pytest.mark.asyncio
async def test_success_carries_the_stored_reference_and_what_produced_it(config: TTSConfig, engine_generate) -> None:
    engine_generate.return_value = _result()

    payload = json.loads(await TTSTool(config).ainvoke({"text": "Hello"}))

    assert payload == {
        "status": "success",
        "provider": "elevenlabs",
        "model": "eleven_flash_v2_5",
        "latency_ms": 150,
        "audio_url": "media://media_speech",
        "message": "Audio generated; it appears in the chat for the user to play.",
        "duration_seconds": 3.2,
    }


@pytest.mark.asyncio
async def test_a_result_without_a_stored_reference_is_an_error(config: TTSConfig, engine_generate) -> None:
    engine_generate.return_value = _result(persisted_ref=None)

    payload = json.loads(await TTSTool(config).ainvoke({"text": "Hello"}))

    assert payload["status"] == "error"
    assert "not stored" in payload["error"]
    assert "audio_url" not in payload


@pytest.mark.asyncio
async def test_an_engine_failure_is_reported_not_swallowed(config: TTSConfig, engine_generate) -> None:
    engine_generate.side_effect = TTSGenerationError("provider down")

    payload = json.loads(await TTSTool(config).ainvoke({"text": "Hello"}))

    assert payload["status"] == "error"
    assert "provider down" in payload["error"]


@pytest.mark.asyncio
async def test_empty_text_is_rejected(config: TTSConfig, engine_generate) -> None:
    payload = json.loads(await TTSTool(config).ainvoke({"text": "   "}))

    assert payload["status"] == "error"
    engine_generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_over_the_limit_is_rejected(config: TTSConfig, engine_generate) -> None:
    payload = json.loads(await TTSTool(config).ainvoke({"text": "x" * (MAX_TTS_TEXT_LENGTH + 1)}))

    assert payload["status"] == "error"
    assert str(MAX_TTS_TEXT_LENGTH) in payload["error"]
    engine_generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_style_is_trimmed_and_clamped_before_it_reaches_the_engine(config: TTSConfig, engine_generate) -> None:
    engine_generate.return_value = _result(duration_seconds=None)

    payload = json.loads(await TTSTool(config).ainvoke({"text": "Hi", "style": "  " + "calm " * 80}))

    engine_generate.assert_awaited_once()
    style = engine_generate.await_args.kwargs["style"]
    assert style == ("calm " * 80).strip()[:200]
    assert len(style) == 200
    assert "duration_seconds" not in payload


async def test_the_result_names_the_copy_of_the_audio_in_the_workspace(
    config: TTSConfig, engine_generate: AsyncMock, library: MediaLibraryEnv, workspace: Path
) -> None:
    record = await library.store(_AUDIO, content_type="audio/mpeg")
    engine_generate.return_value = _result(persisted_ref=media_reference(record.id))

    payload = json.loads(await create_tts_tool(config, allowed_roots=[str(workspace)]).ainvoke({"text": "Hello"}))

    assert payload["status"] == "success"
    assert payload["audio_url"] == media_reference(record.id)
    assert payload["workspace_files"] == [f"./{WORKSPACE_MEDIA_DIR}/{record.id}.mp3"]
    assert (workspace / WORKSPACE_MEDIA_DIR / f"{record.id}.mp3").read_bytes() == _AUDIO


async def test_an_agent_without_a_workspace_gets_the_result_as_it_is(
    config: TTSConfig, engine_generate: AsyncMock, library: MediaLibraryEnv, workspace: Path
) -> None:
    record = await library.store(_AUDIO, content_type="audio/mpeg")
    engine_generate.return_value = _result(persisted_ref=media_reference(record.id))

    payload = json.loads(await create_tts_tool(config).ainvoke({"text": "Hello"}))

    assert "workspace_files" not in payload
    assert "workspace_files_error" not in payload
    assert list(workspace.iterdir()) == []


async def test_a_failed_generation_leaves_the_workspace_alone(
    config: TTSConfig, engine_generate: AsyncMock, workspace: Path
) -> None:
    engine_generate.side_effect = TTSGenerationError("provider down")

    payload = json.loads(await create_tts_tool(config, allowed_roots=[str(workspace)]).ainvoke({"text": "Hello"}))

    assert payload["status"] == "error"
    assert "workspace_files" not in payload
    assert not (workspace / WORKSPACE_MEDIA_DIR).exists()


async def test_a_copy_that_fails_leaves_the_result_as_it_is_and_says_so(
    config: TTSConfig, engine_generate: AsyncMock, workspace: Path
) -> None:
    engine_generate.return_value = _result(persisted_ref=media_reference("media_gone"))

    payload = json.loads(await create_tts_tool(config, allowed_roots=[str(workspace)]).ainvoke({"text": "Hello"}))

    assert payload["status"] == "success"
    assert payload["audio_url"] == "media://media_gone"
    assert payload["workspace_files_error"] == "media://media_gone: it is no longer in the media library"
