# media_tools/

## Overview

Product-layer LangChain adapters for image/video/TTS generation. Engines live in
`myrm-agent-harness/toolkits/llms/{image,video,tts}/`. Every generated artifact is stored in the media library
before a tool or task reports success and is addressed everywhere as `media://<id>`; a failure to store it is a failed
generation, never a result without a file. A finished item is also copied into the agent's workspace, because
`media://` is an address only the media tools can open while bash and file tools need a file.

## Files

| File | Role |
|------|------|
| `__init__.py` | 包级统一出口门面（SSOT Facade），导出 `clamp_image_payload`、`create_image_generation_tool`、`create_video_generation_tool`、`create_tts_tool`、`create_media_persist_callback` |
| `image_clamp.py` | `clamp_image_payload` 纯函数（防 413 Payload 超限、EXIF 拍摄角度物理纠偏、RGBA 透明底板 Alpha 混合合成纯净 RGB、坏图优雅降级与小图无损直通），作为输入解析器的图片处理钩子 |
| `input_policy.py` | `build_media_input_policy`：工具与任务执行器共用的引用解析策略 —— `media://` 加载器、工作区允许根目录、图片钩子；`allowed_roots` 随任务载荷快照，执行器据此解析；`keep_image_size`：同一策略换成只净化不缩小的图片钩子（EXIF 转正与透明合成照旧），带蒙版编辑的原图用它，因为蒙版只适用于同尺寸的图 |
| `task_results.py` | 图片、视频两个工具共用的结果形状：`tool_error`、`media_task_store`（调用时取任务库）、`started_tasks_result`（`tasks[{task_id,task_type,status}]`，并告知模型任务进行中时别反复查询、先告诉用户进行中，只有下一步要用结果或用户要求完成后告知时才用带 `wait_seconds` 的 `status`）、`media_task_status_result`（`status` 动作；可选 `wait_seconds`（1–90 秒，缺省立即返回）每秒读一次任务库，任务结束或时间用完即返回最后状态，任务被删除按「未找到」报错，取消调用即停止等待；失败任务带 `next_step`；传入 `exporter` 时，已成功且属于本工具所在用户与聊天的任务，其结果再带上工作区副本） |
| `workspace_export.py` | `WorkspaceExporter`：把结果里的 `media://` 引用（`video_urls`、`images[].url`、`audio_url`）复制进智能体工作区（`allowed_roots` 的第一个目录）的 `_generated/<id>.<扩展名>`，结果里加 `workspace_files`（形如 `./_generated/<id>.<扩展名>` 的路径列表，相对工作区根；开头的 `./` 让凭据脱敏把它当路径放过，见下文「Workspace copies」）；复制不了的项进 `workspace_files_error`，主结果不变，也不会让调用失败（取消除外）。`_generated/` 经 harness 的 `open_directory_beneath` 打开一次（路径上是链接则拒绝），`.gitignore` 与临时文件都经这个已打开的目录创建，把 `_generated` 换成链接改不了落点；`.gitignore` 只在创建 `_generated/` 时写入，已有的同名目录不动；规则写不进时撤销本次调用建出的规则，并只删空的 `_generated/`（进程之外放进去的内容保留）；同一工作区在本进程内的调用在找目录、建目录、写规则与撤销期间轮流进行（每个工作区根一把进程内的锁，复制本身不在锁内，不同工作区互不等待），本进程内没有调用会遇到另一个调用建了一半的目录，另一个服务进程里的调用不被这把锁拦住；id 与扩展名按白名单校验；先写同一目录下的唯一临时文件再 `os.replace`，已存在的副本不重写；文件系统工作都在线程里。`complete_status` 只处理成功的任务，且任务的 `user_id` 与载荷里的 `chat_id` 必须等于本工具的（两边都没有聊天也算同一信任域），别人的任务按 `task_id` 取回时仍只得到 `media://` |
| `image_agent_tool.py` | `create_image_generation_tool` → `image_tool`：`generate` 每张图一个后台任务（`n` 个任务，不把 `n` 传给供应商）并返回任务列表；`edit` 单图同步返回，要求可编辑的模型，`n>1` 明确报错；`status`（可带 `wait_seconds`，只属于该动作，其他动作带了会明确报错）/ `list`；参数只用官方取值（`quality` 为 low/medium/high 可选，无 `style`）；`quality` 只随 OpenAI 兼容线路（`openai_chat`、`azure_openai`）的生成与改图请求下发，Gemini、Stability、MiniMax 的请求不带它；属于另一个动作的参数（`generate` 带 `image_url` / `mask_url` / `task_id`，`edit` 带 `reference_image_urls` / `task_id`；空串与空列表等于没传）明确报错而不是被丢弃；引用文件由 worker 经统一解析器解析，坏引用让任务失败并指出字段名；带 `mask_url` 的 `edit` 解析原图时不缩小（蒙版仍原样），超过引擎上限的原图由验证器明确报错；`status` 与 `edit` 的结果经 `WorkspaceExporter` 带上 `workspace_files` |
| `media_persist.py` | 三个引擎共用的落库回调：无论有无会话都落库（后台任务没有 `chat_id`），返回 `media://<id>`，落库失败抛错使生成失败 |
| `video_agent_tool.py` | `create_video_generation_tool` → `video_tool`：`generate` 入队一个后台任务并返回任务列表，参考图 / 参考视频由 worker 解析；`status` 查统一任务状态（可带 `wait_seconds` 等任务结束；成功任务的 `result` 带 `workspace_files`）；`list` 列出供应商与模型 |
| `video_schema.py` | `build_video_input_schema`：按活跃 Provider Capabilities 动态裁剪不支持的参数，`ConfigDict(extra="allow")` 超集透传，每个供应商的 schema 固定以保护 Prompt Cache |
| `tts_agent_tool.py` | `create_tts_tool` → `tts_generate`：文本转语音，可选 `style`（仅火山 Seed-Audio 生效），空文本与 1500 字符上限防护，结果带 `audio_url`（`media://`）、`model`、`duration_seconds`、`workspace_files`，只有音频已落库才报成功；工作区副本失败不影响成功 |

## Mount policy

A tool is mounted only when the user has chosen what it runs: an image model in the catalog, a video provider that
serves the chosen model, or a TTS provider/model/voice that the engine supports. Credentials alone mount nothing and no
default is substituted (see `app/core/media/selection.py`). Mounted tools are **AgentDeclared** (eager, Turn 1 schema)
via `general_agent/tool_setup/` — not deferred. The schema text is fixed per configuration, and dynamic guidance
(`next_step` of a failed task, what to do while a started task runs) appears only in tool results, so the stable prompt
prefix does not change between turns.

## Result registration

The stream collector registers the media of each tool call from the tool's structured result
(`app/services/agent/streaming_support/media_generated.py`), so a card never depends on what the model writes. The
collector reads no workspace field: `workspace_files` and `workspace_files_error` change no card, and a `status` call
registers none.

## Workspace copies

`<workspace>/_generated/<media id>.<ext>` holds a real copy (never a link, so editing it cannot reach the library's
original), named after the media so that a repeat call finds it.

The server writes there from outside the agent's sandbox, which can write the same folder. The folder is opened once
with the harness's `open_directory_beneath` (a link on the way is refused), and the `.gitignore` and the temporary
files are created through that open folder: swapping `_generated` for a link between two steps redirects nothing, the
files stay in the folder that was opened. The last step, the rename that publishes a copy, resolves names, so a
swapped folder holds no temporary file for it and nothing is published. Calls on one workspace within this process take
turns while the folder is found, made and given its `.gitignore` (one in-process lock per workspace; the copying itself
runs outside it and calls on other workspaces never wait), so no call of this process meets a folder that another is
still making; a call in another server process is not held back.

The result names it `./_generated/<media id>.<ext>`, relative to the workspace root, and the leading `./` is part of the
contract: the agent stack's credential redaction masks random-looking tokens in tool results, takes the bare
`_generated/media_<random id>.<ext>` for one in most results (a masked path opens no file) and strips a
`./_generated/...` path before it looks for tokens.

The tool descriptions say where the paths are and that a missing `workspace_files` means there is no copy; they never
mention the workspace path itself, so the schema text stays identical across sessions, users and chats and the stable
prompt prefix does not move. An agent that declares no workspace directory gets no field and no error.
