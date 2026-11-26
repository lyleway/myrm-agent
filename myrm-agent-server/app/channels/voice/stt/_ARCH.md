# channels/voice/stt/

## 架构概述

入站语音转文字：入口、本地与云端提供方、模型默认值分层，`__init__.py` 是唯一门面。上级：[../_ARCH.md](../_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 门面 | 导出 transcribe / download_audio / 本地可用性与状态 / default_stt_model | ✅ |
| `common.py` | 辅助 | 音频大小上限、可接受的扩展名与 MIME 表、按 URL / Content-Type 猜扩展名、按路径猜 MIME | ✅ |
| `models.py` | 核心 | 模型默认值与语言字段规则：设置里的模型为空时由 `default_stt_model` 决定（deepgram `nova-3`、groq `whisper-large-v3`、OpenAI `gpt-transcribe`；自定义端点按「OpenAI 兼容服务器」处理，用 `whisper-1`）；`language_fields` 让 `gpt-transcribe*` 用 `languages[]`（小写代码）、其余模型用 `language` | ✅ |
| `local.py` | 核心 | 本地 faster-whisper：进程级模型管理器（懒加载、空闲卸载）、可用性探测与安装提示、`/stt/status` 状态、本地提供方 | ✅ |
| `providers.py` | 核心 | 云端提供方：OpenAI / Groq / 自定义 OpenAI 兼容端点（同一请求格式）、Deepgram、xAI（独立的 `/v1/stt`） | ✅ |
| `core.py` | 核心 | `transcribe`：校验大小、按提供方分发、失败回退（云端失败 → 本地可用则本地；本地失败 → 配了密钥则云端）；`download_audio`：远程音频下载到临时文件 | ✅ |

## 依赖关系

- `core.py` → `common.py`、`local.py`、`providers.py`
- `providers.py` → `common.py`、`models.py`
- `local.py`、`models.py`、`common.py` 不依赖包内其他模块
- 流式端点 `app/api/stt/ws_stream.py` 经门面取 `default_stt_model`；语音会话端点 `app/api/voice/ws_session.py` 自带 Deepgram 流式默认 `nova-3`
