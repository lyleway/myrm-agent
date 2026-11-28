# delegation/

## 架构概述

外部 CLI 直连委托（用户在输入框指定了某个外部 Agent，`force_external_agent`）的业务层（Server）。五件事各一个模块：安全策略放不放行用户的回合并说清拒因（`gate`）、用户回合变成 CLI 的提示词（`prompt`）、回合里的图片落成 CLI 读得到的文件（`images`）、CLI 的回合翻译成前端 SSE 事件（`stream`）、失败说成用户能自己处置的话（`errors`）。池的生命周期与会话持久化在上一级 `external_agents/`；路由编排（重载策略、可用性）在 `ai_agents/general_agent/forced_delegate.py`。外部只从本包门面 `app.services.external_agents.delegation` 导入。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | 门面：导出 `direct_turn_denial` / `Denial` / `DenialCause`、`delegation_prompt`、`stage_images` / `StagedImages`、`stream_direct_delegate`、`SseEvent` 与四个失败指引事件构造 | ✅ |
| `gate.py` | 核心 | `direct_turn_denial(agent, overlay, config_under)`：用户点名外部 Agent 的回合，按模型发起的 `invoke_acp_agent_tool` 同一个工具名、同一份有效策略（通道预设、已保存配置、Agent 覆盖、请求覆盖）、同一个引擎求值；只有 deny 拒绝，ask 是用户点名时已给的确认。拒因靠「放开一项再判一次」归属，不新增判定引擎：请求来自远程连接 → `REMOTE`（本聊天与设置页都放不开）；放开会话预设后不再 deny → `SESSION_PRESET`（切换预设即可）；其余 → `POLICY`（设置里的策略、Agent 自己的规则）。多个原因并存时说最难放开的那个（远程 > 策略 > 预设）：预设之外策略也拒绝时归 `POLICY`，不让用户先去切换一个切了也没用的预设。`Denial.reason` 是引擎原话，只进日志 | ✅ |
| `prompt.py` | 核心 | `delegation_prompt(query, quote)`：把直连委托收到的用户回合变成外部 CLI 只收的那一条文本提示词。文本块按空行连接；图片在 CLI 读得到的位置（`file://` 解码一次成本地路径、http(s) 地址）给一行 `[Image: …]` 指向（带 `video/` MIME 的写 `[Video: …]`），内联数据（data URL）明说没有转交而不贴 base64，认不出的块丢弃，不再把 Python 列表的 repr 当提示词；划词引用用主 Agent 同款 `<quoted_context>` 标签放最前。纯函数，不加路由横幅；`image_url` / `is_video` 是图片内容块 URL 与视频判别的唯一取法，`images.py` 共用 | ✅ |
| `images.py` | 核心 | `stage_images(query)`（异步上下文）：把回合里的图片落成 CLI 读得到的私有文件，并把图片块改指向它，使提示词里的 `[Image: …]` 路径与交给 CLI 的文件是同一个（能收图的后端，Claude、Codex 与声明了图片提示的 ACP 代理，由 harness 按各自的方式带上这张图；收不了图的后端仍有这一行路径，并由界面如实说明）。来源三种：本服务存储 URL（`files_utils.read_image_bytes`，只读本地存储）、`file://`（Tauri 任意位置）、data URL；外部 http(s) 地址从不抓取。按魔数认格式（harness 的 `detect_image_mime` 加 `VISION_IMAGE_MIME_TYPES`：PNG / JPEG / GIF / WebP，扩展名取自字节，不信文件名）、单张上限 5 MiB、视频不处理；文件落在 `staging_dir()` = `<state_dir>/delegation-images`（目录 0700、文件 0600），回合后删除（`finally` + `shield`，取消也清理），崩溃遗留的超过 24 小时的文件由下一次暂存清掉（不跟随符号链接）。任何一张图失败只记一条不含 URL 的 WARNING 并保持原样，不让回合失败；无图回合不碰磁盘 | ✅ |
| `stream.py` | 核心 | `stream_direct_delegate`：直连委托（`force_external_agent`）把 harness `RuntimeEvent` 翻译成前端 SSE 事件。外部工具调用是带 `tool_call_id` 的独立步骤（开始与结果更新同一步，每次更新重发步骤文本），失败带截断的错误文本；子 Agent（Claude 的 `Agent` 调用）做的工具调用带 `parent_step_key`（启动它的那次调用的 `tool_call_id`，每次更新都重发，收集器随步骤持久化），前端据此把它们收在那一步之下；状态步骤（`delegation_<agent>_status`）的措辞全由前端按语言给出，正文只带 CLI 自己给的内容（`working` 的已运行时长、`warning` 的 CLI 原文），`starting` 等其余状态正文为空；会话未被续接（`session_expired` / `session_workspace_changed`）、图片没有交给外部 Agent（harness 的 `images_not_sent` 状态：后端不收图，或 ACP agent 没声明图片提示能力；提示词里仍有图片位置一行）、用户的记忆没有交给外部 Agent（harness 的 `mcp_not_attached` 状态且点名记忆服务器）、交出了记忆却从未连上（回合结束时由 `memory_grants.memory_connected` 判断，发 `mcp_not_connected`；失败的回合只显示自己的错误，已说过没交出的回合不重复说）与回合在中途结束（停在轮数上限 `turn_limit`——`DONE(max_turn_requests)`，Claude 与 ACP Agent 同样、输出被截断 `max_tokens`、Agent 拒绝继续 `refusal`）发只有标题的已完成步骤；轮数上限是正常停止（无错误横幅），`CANCELLED` 与令牌取消以 `CANCELLED` 事件收尾（无 `MESSAGE_END`）；`images` 原样交给池（空列表按无图），池按后端能力决定是否转交；`permissions`（`ChatPermissionBroker`）原样交给池：外部 Agent 要做事时由聊天里的人逐条决定，没有它的回合里 `safe` 的 Agent 只能读；`AUTH_FAILED` / `BACKEND_NOT_FOUND` / `TIMEOUT` 经 `errors.py` 给带处置步骤的错误事件（按会话语言），CLI 的原话改记 WARNING 日志（`redact_sensitive_text` 脱敏、截断到 500 字），其余错误保持 CLI 原话。回合以 `aclosing` 关闭，客户端断开即终止外部进程；`on_token_cancelled` 让外部 Agent 静默时停止按钮同样即时生效 | ✅ |
| `errors.py` | 核心 | 直连委托的失败指引：`delegate_error_event` 把外部 Agent 用户能自己处置的三类失败（未登录 / API Key 被拒、未安装、超时）翻译成带 `diagnostic_result`（`resolution_steps` 恒为数组）与 `recovery_actions`（跳 `/settings/developer`）的错误事件；`agent_unavailable_event`（用户指定的外部 Agent 不可用）、`delegate_denied_event(agent, locale, cause)`（安全策略不允许委托，按拒因给三种文案：策略拒绝按钮跳 `/settings/security`，会话预设说明要切换聊天的安全预设，远程连接说明要在装有该 Agent 的电脑上使用，后两种没有设置按钮；`error_type` 为 `external_agent_denied` / `external_agent_denied_session_preset` / `external_agent_denied_remote`）、`delegate_failed_event`（委托在拿到 CLI 的结果之前就出了意外）给同形状的错误事件，按钮是 `_Action(id, label, url)`。中英两套文案，其他语言回退英文，只说用户要做什么，不带异常名、路径或命令行；API Key 被拒时点名该 Agent 读取的环境变量（`delegate_error_event(api_key_env=…)`，名字由 `stream.py` 按 Agent 的命令从 harness 的 Agent 契约 `driver_for(...).api_key_env` 取得，如 `ANTHROPIC_API_KEY` / `GEMINI_API_KEY / GOOGLE_API_KEY`，指向设置页「环境变量」；认不出的命令回到不点名的通用步骤）；限流、进程崩溃等其余失败返回 `None`，保持 CLI 原话 | ✅ |

## 依赖

- `myrm_agent_harness.api.AgentEventType` / `api.on_token_cancelled` / `acp.types` — `stream.py` 的事件词汇、错误码与令牌取消桥
- `myrm_agent_harness.toolkits.acp.types` — `errors.py` 的错误码
- `myrm_agent_harness.agent.security.{engine,tool_registry,trust_context,types}` — `gate.py` 的求值、工具名到权限的解析、远程请求判别与裁决类型
- `app.core.security.session_overlay.SecurityOverlaySpec` — `gate.py` 的请求覆盖（拒因归属时放开其中一项）
- `myrm_agent_harness.toolkits.acp.drivers.driver_for` — `stream.py` 取 API Key 模式读取的环境变量名
- `app.services.external_agents.memory_grants` — `stream.py` 判断交出了记忆的 Agent 有没有连上
- `app.core.utils.files_utils.read_image_bytes` / `app.config.settings` — `images.py` 读存储图片与定位状态目录
- `myrm_agent_harness.utils.mime_types` — `images.py` 按魔数判图片格式并取扩展名
