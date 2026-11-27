"""Browser socket and Deepgram connection stand-ins for the STT stream and voice session handler tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from websockets import ClientConnection
from websockets.typing import Data, DataLike

from app.channels.types import STTResult, VoiceConfig

Frame = dict[str, object] | Exception


def scripted_receive(frames: Iterable[Frame], *, hang: bool = False) -> Callable[[], Awaitable[dict[str, object]]]:
    """A browser socket's `receive()`: replays `frames` (an exception is raised), then disconnects or, with `hang`, waits forever."""
    queue = list(frames)

    async def receive() -> dict[str, object]:
        if queue:
            frame = queue.pop(0)
            if isinstance(frame, Exception):
                raise frame
            return frame
        if hang:
            await asyncio.Event().wait()
        return {"type": "websocket.disconnect"}

    return receive


def browser_socket(frames: Iterable[Frame] = (), *, hang: bool = False, user_id: str = "") -> AsyncMock:
    ws = AsyncMock()
    ws.receive.side_effect = scripted_receive(frames, hang=hang)
    ws.state = SimpleNamespace(user_id=user_id)
    return ws


def sent_json(ws: AsyncMock) -> list[dict[str, object]]:
    """The JSON messages the handler sent to the browser with `send_text`, in order."""
    return [json.loads(call.args[0]) for call in ws.send_text.await_args_list]


def audio_frame(data: bytes) -> dict[str, object]:
    """A binary audio frame as the browser socket's `receive()` delivers it."""
    return {"type": "websocket.receive", "bytes": data}


def deepgram_results(text: str, **extra: object) -> str:
    """A Deepgram `Results` message with `text` as its only alternative; `extra` adds fields such as `is_final`."""
    return json.dumps({"type": "Results", "channel": {"alternatives": [{"transcript": text}]}, **extra})


@contextmanager
def stored_voice_settings(
    extracted: VoiceConfig | None, *, strict_auth: bool = False, load_error: Exception | None = None
) -> Iterator[None]:
    """Make the handlers' voice config lookup find `extracted` (or fail with `load_error`) under the given auth mode."""
    with (
        patch(
            "app.platform_utils.deployment_capabilities.get_deployment_capabilities",
            return_value=MagicMock(requires_strict_ws_auth=strict_auth),
        ),
        patch(
            "app.core.channel_bridge.config_loader.load_user_configs",
            new_callable=AsyncMock,
            return_value=MagicMock(voice_dict={}),
            side_effect=load_error,
        ),
        patch("app.core.channel_bridge.config_parsers.extract_voice_config", return_value=extracted),
    ):
        yield


class FakeDeepgram(ClientConnection):
    """A Deepgram connection that replays `transcripts`, then raises `then` or hangs, and records what is sent to it."""

    def __init__(
        self,
        *transcripts: Data,
        then: Exception | None = None,
        hang: bool = False,
        send_error: Exception | None = None,
    ) -> None:
        # The real connection needs a live socket; the handlers under test only send messages and iterate.
        self.sent: list[Data] = []
        self._transcripts = transcripts
        self._then = then
        self._hang = hang
        self._send_error = send_error

    async def send(self, message: DataLike | Iterable[DataLike] | AsyncIterable[DataLike], *, text: bool | None = None) -> None:
        if self._send_error is not None:
            raise self._send_error
        assert isinstance(message, (str, bytes)), "the handlers under test send whole messages"
        self.sent.append(message)

    async def __aiter__(self) -> AsyncIterator[Data]:
        for transcript in self._transcripts:
            yield transcript
        if self._then is not None:
            raise self._then
        if self._hang:
            await asyncio.Event().wait()


def control_messages(deepgram: FakeDeepgram) -> list[object]:
    """The JSON control messages (everything but audio) sent to the Deepgram connection, in order."""
    return [json.loads(message) for message in deepgram.sent if isinstance(message, str)]


class DeepgramConnect:
    """Stands in for the object `websockets.connect(...)` returns: an async context manager yielding `connection`."""

    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> ClientConnection:
        return self._connection

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeTranscriber:
    """Stands in for `transcribe`: records the audio file it was given while it still exists, then answers or raises."""

    def __init__(self, text: str | None = "hello", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.path: Path | None = None
        self.audio = b""

    async def __call__(self, path: Path, config: VoiceConfig) -> STTResult | None:
        self.path = path
        self.audio = path.read_bytes()
        if self.error is not None:
            raise self.error
        return STTResult(text=self.text) if self.text is not None else None

    def temp_file_was_removed(self) -> bool:
        return self.path is not None and not self.path.exists()
