# voice/

## Overview
Voice subsystem: STT transcription, TTS synthesis, and voice message routing.

Supports 5 STT providers (local/openai/groq/deepgram/xai) with automatic fallback,
and 6 TTS providers (edge/piper/voicebox/elevenlabs/fish_audio/minimax) with Edge/Piper local free fallback.
Video attachments (.mp4/.webm) are transcribed via the same STT pipeline.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Voice subsystem: STT transcription, TTS synthesis, and voice message routing. | — |
| handler.py | Core | Voice processing module. STT for voice and video, TTS synthesis (audio attachment is `ephemeral`, deleted by the bus after delivery), download dispatching. | ✅ |
| stt/ | Core | 入站语音转文字子包（5 个提供方）：common（大小上限/文件类型）、models（模型默认值与语言字段）、local（faster-whisper）、providers（云端提供方）、core（分发与回退）；支持内存字节输入，零磁盘延迟。详见 [stt/_ARCH.md](stt/_ARCH.md) | ✅ |
| tts/ | Core | 出站文本转语音子包：common（可用性/常量/临时文件）、core（单次/流式入口）、providers（各提供方请求与流式实现）；门面保持原导入面。详见 [tts/_ARCH.md](tts/_ARCH.md) | ✅ |

## Architecture Notes

- **STT Fallback**: mirrors TTS pattern. Primary fails → local if available (free fallback); local fails → cloud API.
- **Local STT**: uses faster-whisper (optional `local-stt` extra). Singleton model loaded once, reused across calls. Install via `uv sync --extra local-stt`. HTTP `/stt/transcribe` and WebSocket `/ws/stt/stream` return install hint when provider is local but deps are missing.
- **Edge TTS**: optional `voice-tts` extra (GPL-3.0, `edge-tts`); free fallback when paid providers fail. Install via `uv sync --extra voice-tts` (not bundled in desktop/commercial PyInstaller builds).
- **Zero-Disk Streaming**: `stt.transcribe` accepts `audio_bytes` in memory to eliminate physical file I/O latency, fully utilized by the Discord provider.
- **Streaming STT**: Deepgram WebSocket (server layer), local uses batch mode only.
- **xAI Grok STT**: dedicated `_transcribe_xai()` for the non-OpenAI-compatible `/v1/stt` endpoint with ITN support.
- **stt_base_url**: `VoiceConfig.stt_base_url` enables any OpenAI-compatible STT endpoint (Mistral, Azure, self-hosted Whisper).
- **STT model default**: `VoiceConfig.stt_model` is empty unless the user typed one; an empty value resolves in `stt/models.py` (OpenAI `gpt-transcribe`, Groq `whisper-large-v3`, Deepgram `nova-3`, custom endpoint `whisper-1`), so no model name is pinned in stored settings.
- **Video Transcription**: `handler.transcribe_video_inbound()` downloads video and reuses the STT pipeline; `.mp4`/`.webm` are natively supported by STT providers.
