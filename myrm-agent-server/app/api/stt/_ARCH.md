# api/stt/

## 架构概述

语音转文字 HTTP/WebSocket 流式层。上级文档：[../_ARCH.md](../_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | 包入口与导出 | — |
| `router.py` | 路由 | Speech-to-Text API；local provider 未装 `local-stt` extra 时 `/transcribe` 返回 503（与 TTS Edge 守卫对称） | ✅ |
| `ws_stream.py` | 模块 | WebSocket STT streaming；local provider 未装 `local-stt` extra 时返回 error JSON + 关闭连接；配置与控制帧经 `_StreamFrame` 校验（不是 JSON 对象或字段类型不对的配置以 `Invalid config` 拒绝，控制帧忽略）；关闭动作只向 Deepgram 发一次 `CloseStream`，浏览器一侧结束后在 `_PROVIDER_FLUSH_TIMEOUT` 内继续中继 Deepgram 随后冲出的最终结果 | ✅ |
