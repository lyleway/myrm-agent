"""A loopback stand-in for the media endpoints a tool gateway setting points at: OpenAI-shaped images and gateway-routed speech.

[INPUT]
- http.server (POS: the loopback server the gateway setting is pointed at)
- PIL (POS: a PNG of the size a request asked for, and the pixel size of the image parts of an edit)
- wave (POS: the WAV clip a speech request is answered with)

[OUTPUT]
- png_bytes() / wav_bytes(): a solid PNG, a clip of silence
- UploadedPart / ProviderRequest: what the gateway saw of one request
- MediaGatewayStub: the requests it saw, the media it answered with, its address and an optional failure status
- serve_media_gateway(): serves a stub on a free loopback port for the length of a with-block
- REFUSAL_MESSAGE: what the gateway says when it refuses a request (it is part of the error the product shows)

[POS]
Shared by the API-level and the Chrome-level media E2Es. The gateway is the only stand-in: everything between the model and the
gateway is the product. Its images have the size they were asked for, so a wrong size in a request shows up in the file the model
measures and in the card the page shows.
"""

from __future__ import annotations

import base64
import io
import json
import threading
import time
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

_EDITS_PATH = "/images/edits"
_SPEECH_SEGMENT = "/tts/"
_FALLBACK_SIZE = (64, 64)
_CLIP_RATE = 8000


REFUSAL_MESSAGE = "the stand-in gateway refuses every request"


def png_bytes(size: tuple[int, int], mode: str = "RGB", color: tuple[int, ...] = (255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, "PNG")
    return buffer.getvalue()


def wav_bytes(seconds: float = 1.0) -> bytes:
    """A mono 16-bit clip of silence a browser decodes and reports a duration for."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(_CLIP_RATE)
        clip.writeframes(b"\x00\x00" * int(seconds * _CLIP_RATE))
    return buffer.getvalue()


@dataclass(frozen=True)
class UploadedPart:
    """A file part of an edit request: its name, its byte count and, when it is an image, its pixel size."""

    filename: str
    size: int
    pixels: tuple[int, int] | None


@dataclass(frozen=True)
class ProviderRequest:
    """One request the gateway received: the JSON body of a generation or a speech request, or the text fields of an edit, and the file parts of an edit."""

    path: str
    authorization: str
    fields: dict[str, object]
    parts: dict[str, UploadedPart]

    @property
    def is_edit(self) -> bool:
        return self.path.endswith(_EDITS_PATH)

    @property
    def is_speech(self) -> bool:
        return _SPEECH_SEGMENT in self.path

    @property
    def requested_size(self) -> tuple[int, int]:
        width, _, height = str(self.fields.get("size")).lower().partition("x")
        return (int(width), int(height)) if width.isdigit() and height.isdigit() else _FALLBACK_SIZE


@dataclass
class MediaGatewayStub:
    """What the stand-in gateway saw and the bytes it answered with.

    ``fail_status`` makes it refuse every request with that status; ``delay_sec`` holds every answer back that long.
    """

    requests: list[ProviderRequest] = field(default_factory=list)
    responses: list[bytes] = field(default_factory=list)
    url: str = ""
    fail_status: int | None = None
    delay_sec: float = 0.0

    @property
    def edits(self) -> list[ProviderRequest]:
        return [seen for seen in self.requests if seen.is_edit]

    @property
    def speeches(self) -> list[ProviderRequest]:
        return [seen for seen in self.requests if seen.is_speech]


def _multipart(content_type: str, body: bytes) -> tuple[dict[str, object], dict[str, UploadedPart]]:
    """The text fields of an edit request and its file parts."""
    message = BytesParser(policy=policy.default).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    fields: dict[str, object] = {}
    parts: dict[str, UploadedPart] = {}
    for part in message.iter_parts():
        name = str(part.get_param("name", header="content-disposition"))
        raw = part.get_payload(decode=True)
        payload = raw if isinstance(raw, bytes) else b""
        filename = part.get_filename()
        if filename is None:
            fields[name] = payload.decode("utf-8")
            continue
        try:
            pixels: tuple[int, int] | None = Image.open(io.BytesIO(payload)).size
        except OSError:
            pixels = None
        parts[name] = UploadedPart(filename, len(payload), pixels)
    return fields, parts


def _handler_for(stub: MediaGatewayStub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _answer(self, status: int, content_type: str, raw: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _answer_json(self, status: int, payload: dict[str, object]) -> None:
            self._answer(status, "application/json", json.dumps(payload).encode("utf-8"))

        def do_POST(self) -> None:  # noqa: N802  (the name http.server dispatches on)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            is_edit = self.path.endswith(_EDITS_PATH)
            fields: dict[str, object]
            parts: dict[str, UploadedPart]
            if is_edit:
                fields, parts = _multipart(self.headers.get("Content-Type", ""), body)
            else:
                fields, parts = json.loads(body or b"{}"), {}
            request = ProviderRequest(self.path, self.headers.get("Authorization", ""), fields, parts)
            stub.requests.append(request)
            time.sleep(stub.delay_sec)
            if stub.fail_status is not None:
                self._answer_json(stub.fail_status, {"error": {"message": REFUSAL_MESSAGE}})
                return
            if request.is_speech:
                clip = wav_bytes()
                stub.responses.append(clip)
                self._answer(200, "audio/wav", clip)
                return
            png = png_bytes(request.requested_size, color=(0, 0, 255) if is_edit else (255, 0, 0))
            stub.responses.append(png)
            self._answer_json(200, {"data": [{"b64_json": base64.b64encode(png).decode("ascii")}]})

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002  (signature of the base class)
            return

    return Handler


@contextmanager
def serve_media_gateway() -> Iterator[MediaGatewayStub]:
    stub = MediaGatewayStub()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(stub))
    stub.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
