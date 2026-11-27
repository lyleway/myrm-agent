"""Real-socket kit for the dictation and voice-session endpoints.

The browser side is a real WebSocket client. The server side is the production STT and voice routers under a real uvicorn
socket, with the voice settings stored through the real ``PUT /config/voice`` route, so a frame travels the path a browser's
does. Deepgram is the one party that cannot be real here (``.env.test`` holds no credentials for it): its stand-in is a
WebSocket server on a real local socket that follows Deepgram's close semantics, ``CloseStream`` makes it flush the results
it still holds and then end the stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import subprocess
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

Event = dict[str, object]

SPEECH_TEXT = "Please remember to buy milk and eggs tomorrow morning."
BROWSER_ORIGIN = "http://localhost:3000"

_SPEECH_VOICE = "Samantha"
_SPEECH_SYNTHESIS_TIMEOUT_SECONDS = 120.0
_SERVER_STARTUP_TIMEOUT_SECONDS = 60.0
_HTTP_TIMEOUT_SECONDS = 30.0
# 250 ms of 16 kHz 16-bit mono audio: the cadence of the browser recorder's chunks.
_AUDIO_CHUNK_BYTES = 8000


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield  # the test runner owns the process's signal handlers


def production_app() -> FastAPI:
    """The routers dictation depends on, mounted on the prefixes ``app/api/router.py`` gives them."""
    from app.api.config.router import router as config_router
    from app.api.stt.ws_stream import router as stt_ws_router
    from app.api.voice.ws_session import router as voice_ws_router

    app = FastAPI()
    app.include_router(config_router, prefix="/api/v1/config")
    app.include_router(stt_ws_router, prefix="/ws/stt")
    app.include_router(voice_ws_router, prefix="/ws/voice")
    return app


@contextlib.asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[str]:
    """Run ``app`` under uvicorn on a free local port and yield its ``host:port``."""
    server = _Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off", timeout_graceful_shutdown=1)
    )
    running = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(_SERVER_STARTUP_TIMEOUT_SECONDS):
            while not server.started:
                if running.done():
                    running.result()
                await asyncio.sleep(0.01)
        yield f"127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        await running


def synthesize_speech(path: Path) -> bytes | None:
    """The 16 kHz mono WAV of a system voice saying ``SPEECH_TEXT``, or None when the host has no speech synthesizer."""
    say = shutil.which("say")
    if say is None:
        return None
    command = [say, "-v", _SPEECH_VOICE, "-o", str(path), "--file-format=WAVE", "--data-format=LEI16@16000", SPEECH_TEXT]
    done = subprocess.run(command, capture_output=True, check=False, timeout=_SPEECH_SYNTHESIS_TIMEOUT_SECONDS)
    return path.read_bytes() if done.returncode == 0 and path.exists() else None


def voice_settings(*, provider: str, api_key: str = "") -> dict[str, object]:
    """The ``voice`` settings the voice settings page saves with speech-to-text switched on."""
    return {
        "sttEnabled": True,
        "ttsMode": "off",
        "ttsProvider": "edge",
        "ttsVoice": "",
        "ttsSpeed": 1.0,
        "ttsPitch": 0,
        "sttProvider": provider,
        "sttApiKey": api_key,
        "sttModel": "",
        "sttLanguage": "",
        "sttLocalModel": "base",
        "sttLocalDevice": "auto",
        "sttLocalComputeType": "auto",
        "sttBaseUrl": "",
        "ttsApiKey": "",
        "ttsBaseUrl": "",
        "ttsMaxLength": 4000,
        "ttsSummaryEnabled": True,
        "ttsSummaryThreshold": 1500,
        "ttsSummaryModel": "",
        "geminiLiveModel": "gemini-2.5-flash-preview-native-audio-dialog",
    }


async def save_voice_settings(host: str, settings: dict[str, object]) -> None:
    """Store ``settings`` the way the settings page does."""
    async with httpx.AsyncClient(base_url=f"http://{host}", timeout=_HTTP_TIMEOUT_SECONDS) as client:
        reply = await client.put("/api/v1/config/voice", json={"deviceId": "web", "value": settings})
    reply.raise_for_status()


@contextlib.asynccontextmanager
async def browser(host: str, path: str) -> AsyncIterator[ClientConnection]:
    """A page's WebSocket to ``path``; it connects with the page's Origin, as every browser does."""
    async with connect(f"ws://{host}{path}", origin=BROWSER_ORIGIN, open_timeout=_HTTP_TIMEOUT_SECONDS) as ws:
        yield ws


async def send_audio(ws: ClientConnection, audio: bytes) -> None:
    """Send ``audio`` in the chunk size the browser recorder emits."""
    for start in range(0, len(audio), _AUDIO_CHUNK_BYTES):
        await ws.send(audio[start : start + _AUDIO_CHUNK_BYTES])


def parse_event(raw: str | bytes) -> Event:
    assert isinstance(raw, str), "the server sent a binary frame where a JSON event was expected"
    event = json.loads(raw)
    assert isinstance(event, dict)
    return event


async def receive_event(ws: ClientConnection, *, timeout: float = _HTTP_TIMEOUT_SECONDS) -> Event:
    return parse_event(await asyncio.wait_for(ws.recv(), timeout))


async def receive_until_closed(ws: ClientConnection, *, timeout: float = _HTTP_TIMEOUT_SECONDS) -> list[Event]:
    """Every event the server sends before it closes the socket normally."""
    events: list[Event] = []
    async with asyncio.timeout(timeout):
        async for raw in ws:
            events.append(parse_event(raw))
    return events


async def until(condition: Callable[[], bool], *, timeout: float = _HTTP_TIMEOUT_SECONDS) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


@dataclass(slots=True)
class ProviderSession:
    """What one upstream connection saw: the request that opened it, the audio and the text frames it received."""

    path: str
    authorization: str
    audio_bytes: int = 0
    text_frames: list[Event] = field(default_factory=list)
    ended: asyncio.Event = field(default_factory=asyncio.Event)


class DeepgramStandIn:
    """Answers the first audio with ``interim``; on ``CloseStream`` it flushes ``flushed`` as finals, sends metadata and ends."""

    def __init__(self, *, interim: str, flushed: Sequence[str], flush_delay: float = 0.2) -> None:
        self.url = ""
        self.sessions: list[ProviderSession] = []
        self._interim = interim
        self._flushed = flushed
        self._flush_delay = flush_delay

    async def handle(self, connection: ServerConnection) -> None:
        request = connection.request
        assert request is not None
        session = ProviderSession(path=request.path, authorization=request.headers.get("Authorization", ""))
        self.sessions.append(session)
        interim_sent = False
        try:
            async for message in connection:
                if isinstance(message, bytes):
                    session.audio_bytes += len(message)
                    if not interim_sent:
                        interim_sent = True
                        await connection.send(_results(self._interim, is_final=False))
                    continue
                frame = parse_event(message)
                session.text_frames.append(frame)
                if frame.get("type") == "CloseStream":
                    await self._flush_and_end(connection)
                    return
        except ConnectionClosed:
            pass  # the server under test left before the stand-in finished
        finally:
            session.ended.set()

    async def _flush_and_end(self, connection: ServerConnection) -> None:
        await asyncio.sleep(self._flush_delay)
        for text in self._flushed:
            await connection.send(_results(text, is_final=True))
        await connection.send(json.dumps({"type": "Metadata"}))
        await connection.close()


def _results(text: str, *, is_final: bool) -> str:
    return json.dumps({"type": "Results", "is_final": is_final, "channel": {"alternatives": [{"transcript": text}]}})


@contextlib.asynccontextmanager
async def deepgram_stand_in(*, interim: str, flushed: Sequence[str], flush_delay: float = 0.2) -> AsyncIterator[DeepgramStandIn]:
    """Serve a ``DeepgramStandIn`` on a free local port; its ``url`` is where the server under test must connect."""
    stand_in = DeepgramStandIn(interim=interim, flushed=flushed, flush_delay=flush_delay)
    server = await serve(stand_in.handle, "127.0.0.1", 0)
    try:
        stand_in.url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1/listen"
        yield stand_in
    finally:
        server.close()
        await server.wait_closed()
