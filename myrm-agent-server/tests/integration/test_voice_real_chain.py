"""Voice endpoints over real sockets: browser client → production routers → settings in the database → speech-to-text.

Nothing between the browser and the provider is replaced: the WebSocket / HTTP client and the uvicorn server talk over real
TCP, the settings travel through ``PUT /config/voice`` into the test database (no LLM provider is configured, as for a user
who has set up voice first), and the key-less ``local`` provider runs the real faster-whisper model on real speech (a system
voice reading a sentence). The three tests that need the speech skip on a host without ``say`` or without the cached ``base``
model. Deepgram cannot be real here (``.env.test`` holds no credentials for it), so its tests face a stand-in WebSocket server
that follows Deepgram's close semantics: what the stand-in transcribes is scripted, what the server does with the frames, the
close and the disconnects is the code under test.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Iterator, Sequence

import pytest
from myrm_agent_harness.core.features import _reset_for_testing

from app.api.voice import ws_session_batch
from app.channels.voice.stt import models as stt_models
from tests.integration.voice_real_chain_testkit import (
    DeepgramStandIn,
    browser,
    deepgram_stand_in,
    http_client,
    production_app,
    receive_event,
    receive_until_closed,
    save_voice_settings,
    send_audio,
    serving,
    until,
    voice_settings,
)
from tests.support.feature_flags import seed_voice_interaction_flags
from tests.support.spoken_audio import SPOKEN_WORDS, synthesize_speech, whisper_base_model_cached

pytestmark = pytest.mark.integration

_API_KEY = "dg-test-key"
_INTERIM = "buy milk"
_LAST_WORDS = "and eggs"
_INTERIM_EVENT = {"type": "transcript", "text": _INTERIM, "is_final": False}
_LAST_WORDS_EVENT = {"type": "transcript", "text": _LAST_WORDS, "is_final": True}
_CLOSE_STREAM = {"type": "CloseStream"}
_BATCH_INFO = {"type": "info", "message": "streaming_unavailable", "fallback": "batch"}
# The stand-in does not decode what it receives, so the bytes that reach it only have to arrive.
_AUDIO_STAND_IN = bytes(24_000)
_MODEL_TIMEOUT_SECONDS = 240.0
# A quiet session's beats come well inside the idle timeout, and run for twice as long as it.
_IDLE_TIMEOUT_SECONDS = 1.5
_HEARTBEAT_SECONDS = 0.4
_HEARTBEATS = 8
# What the browser sees when the server ends a dictation / a voice session on its own terms: a clean close, not a dropped socket.
_DICTATION_CLOSED = (1000, "Done")
_SESSION_CLOSED = (1000, "Session ended")


@pytest.fixture(autouse=True)
def _voice_interaction_enabled() -> Iterator[None]:
    seed_voice_interaction_flags()
    yield
    _reset_for_testing()


@pytest.fixture(scope="session")
def spoken_wav(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    audio = synthesize_speech(tmp_path_factory.mktemp("speech") / "speech.wav")
    if audio is None:
        pytest.skip("no system speech synthesizer (`say`) to produce real speech")
    return audio


@pytest.fixture
def local_whisper() -> Iterator[None]:
    """The real faster-whisper ``base`` model, released after the test so it does not outlive the event loop."""
    pytest.importorskip("faster_whisper")
    if not whisper_base_model_cached():
        pytest.skip("the faster-whisper `base` model is not cached; the test does not download it")
    from app.channels.voice.stt.local import _whisper_manager

    yield
    _whisper_manager.unload()


@contextlib.asynccontextmanager
async def deepgram_stack(
    monkeypatch: pytest.MonkeyPatch, *, flushed: Sequence[str]
) -> AsyncIterator[tuple[str, DeepgramStandIn]]:
    """The served routers with Deepgram settings saved, and the stand-in the server connects to instead of Deepgram."""
    async with deepgram_stand_in(interim=_INTERIM, flushed=flushed) as stand_in, serving(production_app()) as host:
        monkeypatch.setattr(stt_models, "_DEEPGRAM_STREAM_URL", stand_in.url)
        await save_voice_settings(host, voice_settings(provider="deepgram", api_key=_API_KEY))
        yield host, stand_in


def _assert_spoken(text: object) -> None:
    assert isinstance(text, str)
    assert all(word in text.lower() for word in SPOKEN_WORDS), text


async def test_dictation_transcribes_real_speech_with_the_local_model(spoken_wav: bytes, local_whisper: None) -> None:
    async with serving(production_app()) as host:
        await save_voice_settings(host, voice_settings(provider="local"))
        async with browser(host, "/ws/stt/stream") as ws:
            await ws.send(json.dumps({"keyterms": []}))
            assert await receive_event(ws) == _BATCH_INFO
            await send_audio(ws, spoken_wav)
            await ws.send(json.dumps({"action": "close"}))
            events = await receive_until_closed(ws, timeout=_MODEL_TIMEOUT_SECONDS)
            assert (ws.close_code, ws.close_reason) == _DICTATION_CLOSED

    [transcript] = events
    assert transcript["type"] == "transcript"
    assert transcript["is_final"] is True
    _assert_spoken(transcript["text"])


async def test_voice_session_transcribes_each_utterance_with_the_local_model(spoken_wav: bytes, local_whisper: None) -> None:
    async with serving(production_app()) as host:
        await save_voice_settings(host, voice_settings(provider="local"))
        async with browser(host, "/ws/voice/session") as ws:
            await ws.send(json.dumps({"type": "config", "mode": "audio_only", "keyterms": []}))
            assert await receive_event(ws) == _BATCH_INFO
            utterances = []
            for _ in range(2):
                await send_audio(ws, spoken_wav)
                await ws.send(json.dumps({"type": "end_utterance"}))
                utterances.append(await receive_event(ws, timeout=_MODEL_TIMEOUT_SECONDS))
            await ws.send(json.dumps({"type": "close"}))
            assert await receive_until_closed(ws) == []
            assert (ws.close_code, ws.close_reason) == _SESSION_CLOSED

    for utterance in utterances:
        assert utterance["type"] == "stt_final"
        _assert_spoken(utterance["text"])


async def test_voice_session_stays_open_while_the_browser_keeps_ending_empty_utterances(monkeypatch: pytest.MonkeyPatch) -> None:
    """A quiet session uploads no audio, only a bare ``end_utterance`` now and then: skipped, yet proof the browser is there."""
    monkeypatch.setattr(ws_session_batch, "_SESSION_IDLE_TIMEOUT", _IDLE_TIMEOUT_SECONDS)
    async with serving(production_app()) as host:
        await save_voice_settings(host, voice_settings(provider="local"))
        async with browser(host, "/ws/voice/session") as ws:
            await ws.send(json.dumps({"type": "config", "mode": "audio_only", "keyterms": []}))
            assert await receive_event(ws) == _BATCH_INFO
            for _ in range(_HEARTBEATS):
                await asyncio.sleep(_HEARTBEAT_SECONDS)
                await ws.send(json.dumps({"type": "end_utterance"}))
            assert ws.close_code is None
            # The beats stop: the same session now counts as abandoned.
            assert await receive_until_closed(ws) == []
            assert (ws.close_code, ws.close_reason) == _SESSION_CLOSED


async def test_the_upload_route_transcribes_real_speech_with_the_local_model(spoken_wav: bytes, local_whisper: None) -> None:
    async with serving(production_app()) as host:
        await save_voice_settings(host, voice_settings(provider="local"))
        async with http_client(host, timeout=_MODEL_TIMEOUT_SECONDS) as page:
            reply = await page.post("/api/v1/stt/transcribe", files={"file": ("dictation.wav", spoken_wav, "audio/wav")})

    assert reply.status_code == 200
    _assert_spoken(reply.json()["text"])


async def test_read_aloud_finds_the_saved_voice_settings() -> None:
    """The provider override is invalid on purpose: the route rejects it only after it has loaded the saved settings."""
    async with serving(production_app()) as host:
        await save_voice_settings(host, voice_settings(provider="local"))
        async with http_client(host) as page:
            reply = await page.post("/api/v1/tts/synthesize", json={"text": "Hello", "provider": "no-such-provider"})

    assert (reply.status_code, reply.json()["detail"]) == (400, "Invalid provider: no-such-provider")


async def test_dictation_delivers_the_final_results_the_provider_flushes_after_close(monkeypatch: pytest.MonkeyPatch) -> None:
    async with deepgram_stack(monkeypatch, flushed=[_LAST_WORDS]) as (host, provider):
        async with browser(host, "/ws/stt/stream") as ws:
            await ws.send(json.dumps({"keyterms": ["Myrm"]}))
            await send_audio(ws, _AUDIO_STAND_IN)
            assert await receive_event(ws) == _INTERIM_EVENT
            await ws.send(json.dumps({"action": "close"}))
            events = await receive_until_closed(ws)
            assert (ws.close_code, ws.close_reason) == _DICTATION_CLOSED
        [session] = provider.sessions
        await asyncio.wait_for(session.ended.wait(), 30)

    assert events == [_LAST_WORDS_EVENT]
    assert session.authorization == f"Token {_API_KEY}"
    assert "keywords=Myrm%3A2" in session.path
    assert session.audio_bytes == len(_AUDIO_STAND_IN)
    assert session.text_frames == [_CLOSE_STREAM]


async def test_dictation_ignores_malformed_control_frames_and_still_flushes(monkeypatch: pytest.MonkeyPatch) -> None:
    malformed = ["not json", "[1, 2]", '{"action": 5}', '{"keyterms": "x"}']
    async with deepgram_stack(monkeypatch, flushed=[_LAST_WORDS]) as (host, provider):
        async with browser(host, "/ws/stt/stream") as ws:
            await ws.send(json.dumps({"keyterms": []}))
            for frame in malformed:
                await ws.send(frame)
            await send_audio(ws, _AUDIO_STAND_IN)
            assert await receive_event(ws) == _INTERIM_EVENT
            await ws.send(json.dumps({"action": "close"}))
            events = await receive_until_closed(ws)
            assert (ws.close_code, ws.close_reason) == _DICTATION_CLOSED
        [session] = provider.sessions
        await asyncio.wait_for(session.ended.wait(), 30)

    assert events == [_LAST_WORDS_EVENT]
    assert session.text_frames == [_CLOSE_STREAM]


async def test_dictation_cleans_up_the_provider_when_the_browser_vanishes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="app.api.stt.ws_stream")
    async with deepgram_stack(monkeypatch, flushed=[_LAST_WORDS]) as (host, provider):
        async with browser(host, "/ws/stt/stream") as ws:
            await ws.send(json.dumps({"keyterms": []}))
            await send_audio(ws, _AUDIO_STAND_IN)
            assert await receive_event(ws) == _INTERIM_EVENT
            ws.transport.abort()  # a crashed tab or a dropped network: no close frame reaches the server
        [vanished] = provider.sessions
        await asyncio.wait_for(vanished.ended.wait(), 30)

        async with browser(host, "/ws/stt/stream") as ws:
            await ws.send(json.dumps({"keyterms": []}))
            await send_audio(ws, _AUDIO_STAND_IN)
            assert await receive_event(ws) == _INTERIM_EVENT
            await ws.send(json.dumps({"action": "close"}))
            events = await receive_until_closed(ws)
            assert (ws.close_code, ws.close_reason) == _DICTATION_CLOSED
        await until(lambda: len(provider.sessions) == 2 and provider.sessions[1].ended.is_set())

    assert vanished.text_frames == [_CLOSE_STREAM]
    assert events == [_LAST_WORDS_EVENT]
    assert provider.sessions[1].text_frames == [_CLOSE_STREAM]
    assert [record.getMessage() for record in caplog.records if record.name == "app.api.stt.ws_stream"] == []


async def test_voice_session_closes_the_provider_once_and_drops_results_after_close(monkeypatch: pytest.MonkeyPatch) -> None:
    async with deepgram_stack(monkeypatch, flushed=[_LAST_WORDS]) as (host, provider):
        async with browser(host, "/ws/voice/session") as ws:
            await ws.send(json.dumps({"type": "config", "mode": "audio_only", "keyterms": []}))
            await send_audio(ws, _AUDIO_STAND_IN)
            assert await receive_event(ws) == {"type": "stt_interim", "text": _INTERIM}
            await ws.send(json.dumps({"type": "close"}))
            assert await receive_until_closed(ws) == []
            assert (ws.close_code, ws.close_reason) == _SESSION_CLOSED
        [session] = provider.sessions
        await asyncio.wait_for(session.ended.wait(), 30)

    assert session.text_frames == [_CLOSE_STREAM]
