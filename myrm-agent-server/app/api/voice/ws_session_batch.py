"""Batch speech-to-text half of the voice session WebSocket.

For providers without live streaming the session collects the audio of one utterance, transcribes it in a single
request when the browser sends ``end_utterance`` and hands the text to the Agent bridge or back to the browser.

[INPUT]
- app.api.voice.ws_frames::parse_session_frame (POS: validated control frames from the browser)
- app.channels.voice.stt::transcribe (POS: batch speech-to-text)

[OUTPUT]
- BatchSttMixin: `_run_batch_stt` loop and `_batch_transcribe` for `_VoiceSession`

[POS]
Mixin of `app.api.voice.ws_session._VoiceSession`; reads the session's socket, closed flag, voice config and Agent bridge.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocketDisconnect

from app.api.voice.ws_frames import parse_session_frame

logger = logging.getLogger(__name__)

_SESSION_IDLE_TIMEOUT = 180.0


class BatchSttMixin:
    __slots__ = ()

    async def _run_batch_stt(self) -> None:
        """Batch STT for providers without streaming support."""
        await self._send_json(
            {
                "type": "info",
                "message": "streaming_unavailable",
                "fallback": "batch",
            }
        )

        try:
            while not self._closed:
                chunks: list[bytes] = []
                total_size = 0

                while True:
                    try:
                        msg = await asyncio.wait_for(self._ws.receive(), timeout=_SESSION_IDLE_TIMEOUT)
                    except asyncio.TimeoutError:
                        self._closed = True
                        return

                    if msg.get("type") == "websocket.disconnect":
                        self._closed = True
                        return

                    if "bytes" in msg and msg["bytes"]:
                        chunk = msg["bytes"]
                        total_size += len(chunk)
                        if total_size > 25 * 1024 * 1024:
                            await self._send_json(
                                {
                                    "type": "error",
                                    "message": "Audio too large",
                                }
                            )
                            return
                        chunks.append(chunk)

                    elif "text" in msg and msg["text"]:
                        self._handle_text_message(msg["text"])
                        if self._closed:
                            return
                        frame = parse_session_frame(msg["text"])
                        if frame is not None and frame.type == "end_utterance":
                            break

                if not chunks:
                    continue

                audio_blob = b"".join(chunks)
                if len(audio_blob) < 1024:
                    continue

                text = await self._batch_transcribe(audio_blob)
                if text:
                    if self._agent_bridge:
                        task = asyncio.create_task(self._agent_bridge.handle_stt_final(text))
                        task.add_done_callback(self._on_bridge_task_done)
                    else:
                        await self._send_json({"type": "stt_final", "text": text})

        except WebSocketDisconnect:
            pass

    async def _batch_transcribe(self, audio_blob: bytes) -> str | None:
        """Transcribe audio blob using batch STT."""
        import tempfile
        from pathlib import Path

        from app.channels.voice.stt import transcribe

        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(audio_blob)
            tmp_path = Path(tmp.name)

        try:
            result = await transcribe(tmp_path, self._voice_config)
            return result.text if result and result.text else None
        except Exception:
            logger.exception("Batch STT failed in voice session")
            return None
        finally:
            tmp_path.unlink(missing_ok=True)
