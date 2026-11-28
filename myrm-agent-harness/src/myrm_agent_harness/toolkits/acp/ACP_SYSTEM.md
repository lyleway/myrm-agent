# ACP 系统设计方案

> Server 与 Runtime 分层，横切能力开箱即用。

---

## 1. 设计原则

- **Runtime 独立层**：对外部 Agent 统一抽象为 `RuntimeBackend`（ACP / CLI / app-server）
- **禁止过度设计**：每个组件有明确职责
- **Python 原生**：asyncio、Protocol、dataclass、StrEnum，无额外编排框架依赖
- **Server 层稳定**：server/ 子目录（server.py / bridge.py / event_translator.py）专注 IDE ↔ 宿主 Agent 桥接

---

## 2. 架构总览

```
╔═══════════════════════════════════════════════════════════════╗
║                     ACP 系统架构                              ║
╠═══════════════════════════════╦═══════════════════════════════╣
║  Server 层 (server/)           ║  Runtime 层 (runtime/)         ║
║  server.py                    ║  RuntimeBackend Protocol       ║
║  bridge.py                    ║  ├─ AcpRuntime + AcpCallback   ║
║  event_translator.py          ║  ├─ CliRuntime                 ║
║                               ║  └─ CodexRuntime               ║
╠═══════════════════════════════╩═══════════════════════════════╣
║                    横切关注点 (core/)                          ║
║  types.py · core/probe                                       ║
║  core/BackendDetector                                         ║
╚═══════════════════════════════════════════════════════════════╝
```

---

## 3. 核心模块设计

### 3.1 RuntimeBackend Protocol（核心抽象）

**价值**：将 ACP 协议连接与 CLI spawn 统一为一个接口，新增后端只需实现这个 Protocol。

```
RuntimeBackend (Protocol)
├── name: str                                    # 后端标识
├── capabilities: BackendCapabilities            # 能力声明
├── is_alive: bool                               # 存活检查
├── run_turn(prompt, session_id, **) -> AsyncGenerator[RuntimeEvent]  # 核心执行；消费方提前停止时用 aclosing 关闭
├── cancel(session_id) -> None                   # 取消当前轮次
├── resume(session_id) -> bool                   # 恢复会话
├── get_info() -> BackendInfo                    # 后端元数据
└── close() -> None                              # 关闭连接
```

**设计亮点**：
- `cancel()` — 完整的生命周期控制，用户可中断长任务
- `get_info()` → `BackendInfo` — 后端元数据（版本、能力、状态），支持运行时能力查询

**BackendCapabilities**：
- `supports_resume`：是否支持会话恢复
- `supports_mcp`：是否支持 MCP Server 注入
- `supports_streaming`：是否支持流式传输
- `supports_tools`：是否支持原生工具调用
- `supports_permission_requests`：`safe` 下是否会把权限请求交给回合的 `PermissionBroker`（Claude CLI、ACP 后端——含 Gemini、Qwen——与 Codex app server 为真）
- `supports_images`：是否接收随回合附带的图片文件（`run_turn(images=…)`；`CliRuntime` 对 Claude 为真、其余 CLI 为假；`CodexRuntime` 恒为真（图片以文件路径写进 `turn/start`）；`AcpRuntime` 恒为真，是否真的发送由该 agent 在 `initialize` 里声明的 `promptCapabilities.image` 决定）

**3 种实现**：

| Runtime | 协议 | 适用场景 |
|---------|------|---------|
| AcpRuntime | ACP JSON-RPC (stdin/stdout) | 原生就说 ACP 的 Agent（Gemini `--acp`、Qwen `--acp`、opencode `acp`）与 ACP 桥接器（claude-agent-acp / acpx） |
| CliRuntime | spawn CLI + NDJSON 解析（含 reasoning/usage/工具调用）；Claude 经 CLI 自己的会话 id 跨回合续接 | Claude Code（开箱即用）与其他打印 NDJSON 或纯文本的 CLI |
| CodexRuntime | 常驻 `codex app-server` 进程，stdin/stdout 上的 JSON-RPC：一个对话一个线程（thread），一条提示词一个回合（turn），审批策略与沙箱随每个回合给出 | Codex |

**已知 Agent 走哪条通道由契约决定**：`auth` 契约（`AuthProfile.transport`）规定 Claude Code 每回合拉起进程（`cli`），Gemini、Qwen 走 ACP（`acp`），Codex 走它自己的 app server（`app_server`）。命令恰好是这个 Agent 的可执行文件名（任意路径、任意扩展名）时，`RuntimePool.register` 用契约的通道，配置里存下的 `type` 改不了它（旧配置里写成 `cli` 的 `gemini` 无需迁移就走 ACP，并记 INFO `pool_transport_by_contract`）；名字里只是带着 `claude` / `gemini` 的适配器或包装脚本（`claude-code-acp`、`gemini-wrapper`）没有契约，保持配置里的通道与参数。

**BackendInfo**：
```
BackendInfo
├── name: str
├── version: str | None
├── backend_type: "acp" | "cli" | "app_server"
├── status: "ready" | "starting" | "error" | "stopped"
├── capabilities: BackendCapabilities
└── metadata: dict[str, object] | None
```

### 3.2 RuntimeEvent（统一事件模型）

```
RuntimeEvent (frozen dataclass)
├── type: RuntimeEventType
├── data: dict[str, object]
├── session_id: str
└── timestamp: float
```

**RuntimeEventType 枚举（8 种）**：

| 事件类型 | 说明 | data 字段 |
|---------|------|----------|
| `text_delta` | LLM 文本增量 | content: str |
| `reasoning_delta` | 思维链增量 | content: str |
| `tool_start` | 工具调用开始 | tool_name, tool_input, tool_call_id, summary（一行摘要，可为空） |
| `tool_result` | 工具调用结果 | tool_call_id, output（CLI 后端截断到 2000 字符）, is_error |
| `usage_update` | Token 用量更新 | input_tokens, output_tokens, cache_read, cache_write |
| `status_update` | 后端状态变化 | status: str, message: str &#124; None |
| `error` | 错误事件 | error: AcpError |
| `done` | 会话结束 | stop_reason, total_usage |

**设计亮点**：
- `reasoning_delta` — 支持思维链展示
- `usage_update` — 实时 Token 监控
- `status_update` — 后端状态变化通知（starting, compacting, reconnecting 等），UI 可精确展示后端状态；会话连续性通知由 `types.ContinuityStatus` 定义：`session_expired`（CLI、Codex 或 ACP Agent 已不认识上次的会话，本回合改开新会话）与 `session_workspace_changed`（工作目录变了，不续接），宿主据此提示用户而不是当作错误；图片没有交给 agent 时（后端不收图，或 ACP agent 没声明 `promptCapabilities.image`）回合以一条 `images_not_sent`（`types.IMAGES_NOT_SENT_STATUS`，带 `images` 张数）开头，由 `types.images_not_sent_event` 构造，宿主据此如实告诉用户
- `done` 的 `stop_reason` 由 `types.StopReason` 认得：`max_tokens`（输出被截断）、`max_turn_requests`（Agent 自己的回合上限）与 `refusal`（Agent 拒绝继续）是人需要知道的提前结束，各对应一条只带标题的通知（`max_tokens`、`turn_limit`、`refusal`），`end_turn` 与 `cancelled` 不产生通知，不认得的取值按没有原因处理；经 delegate 工具返回给调用模型的结果末尾另加一行注记（`delegate_report.STOP_NOTES`），免得把半截回复当作整段
- 权限请求不是事件：它是回合内的一次问答，由 `PermissionBroker` 回答（见 3.4），所以事件流里没有它

### 3.3 结构化错误体系

```
AcpErrorCode (StrEnum)
├── AUTH_FAILED         # 凭据缺失或被拒（Claude 没登录或 API 回 401、Codex 的 401；CLI 连续重试 401 时运行时提前结束回合）
├── BACKEND_NOT_FOUND   # CLI 未安装（拉起进程时程序不存在）
├── TIMEOUT             # 整个回合超过 timeout_seconds
├── RATE_LIMITED        # 超频
├── PROCESS_CRASHED     # 进程崩溃
├── CANCELLED           # 用户取消
└── UNKNOWN             # 未知错误

AcpError (frozen dataclass)
├── code: AcpErrorCode
├── message: str
├── retryable: bool
└── details: dict[str, object] | None
```

**价值**：生产系统必须有结构化错误码，上层可根据 `retryable` 自动重试，根据 `code` 精准定位问题。

### 3.4 PermissionBroker（权限请求的宿主入口）

**价值**：外部 Agent 要做超出读取的事时，由宿主让人逐条决定。框架只给一个窄协议，不依赖任何审批实现，所以宿主以外的使用者（脚本、另一套产品）同样能接入。

```
PermissionBroker (Protocol)
└── decide(request: PermissionRequest) -> PermissionVerdict    # 等人决定；人要多久由宿主定

PermissionRequest (frozen dataclass)   # agent、session_id（宿主给该回合的 id）、tool_name、kind、title、input、paths、tool_call_id
PermissionVerdict (frozen dataclass)   # allowed；message 是拒绝的理由，只有能被告知的 Agent 才收到
```

**`TurnPermissions`（`runtime/turn_permissions.py`）**：把宿主的 broker 包成回合内可放心使用的版本，后端不必再处理三件事：

- broker 抛异常按拒绝处理（记 `permission_broker_failed` WARNING）；
- 等人决定期间，回合的时间上限（`timeout_seconds`，即 Agent 干活的时间）暂停，人答复后剩余预算原样恢复——人想多久由宿主的 broker 定，不会把干活的时间花在等人上；
- 回合结束时仍在等的请求被取消，此后到达的请求直接拒绝，不会再有人被问一个已经结束的回合。

没有 broker 的回合（定时、后台任务）没有人可问：`safe` 下的 Agent 只能读取。

### 3.5 权限模式（permission_mode）

**两种模式**（`PermissionMode = Literal["safe", "allow_all"]`，库默认 `safe`）：

- `safe`：外部 Agent 不经人同意不能改任何东西。能询问的 Agent 逐条询问，不能询问的 Agent 只能读取。
- `allow_all`：Agent 无人值守地做事。

模式名只有这两个；宿主存下来的旧词表（`ask`、`bypass`）不属于库，由业务层在读取处归为 `safe`。

**`safe` 怎么问**，按后端（`BackendCapabilities.supports_permission_requests` 声明该后端会不会问）：

| 后端 | `safe` 的做法 | 没有 broker 时 |
|------|--------------|----------------|
| ACP（`runtime/acp/permission_policy.py::AcpPermissionPolicy`） | 读类动作（`read` / `search` / `think`）直接放行；其余每个 `session/request_permission` 变成一条 `PermissionRequest` 交给 broker，答复映射回 Agent 自己给的 allow / reject 选项（没有合适的选项就取消并记 `acp_permission_no_matching_option`）。人同意过的编辑记下它点名的文件，随后的 `fs/write_text_file` 不再问第二次；Agent 不先请求权限、直接写文件时，写之前问一次 | 拒绝（`acp_permission_refused_unattended`） |
| Claude CLI（`runtime/cli/permission_bridge.py::PermissionBridge`） | `--permission-mode default` 加 `--permission-prompt-tool`：本回合起一个只监听 127.0.0.1 的 MCP 端点（端口由系统分配、每回合随机 bearer token、`0600` 的 `--mcp-config` 文件，回合结束即清除），Claude 把每个需要权限的工具调用交给它的 `approve(tool_name, input, tool_use_id)`，答案是 Claude 照办的 JSON：`allow` 带原输入，或 `deny` 带模型会读到的理由 | 不起端点，Claude 保持 `default` 的拒绝 |
| Codex（`runtime/codex/approvals.py::ApprovalResponder`） | 每个回合以审批策略 `on-request` 加只读沙箱启动（Codex 自己的 Read Only 预设）：只读动作在沙箱里静默运行，写入、联网、补丁与沙箱外的命令由 Codex 发来审批请求，变成一条 `PermissionRequest` 交给 broker，答复回给 Codex 的 accept / decline（补丁的每处改动带一段有界的摘录）；回合被停止时仍在等的请求答复 cancel，立即结束等待；MCP 工具调用的审批同样交给 broker。是否升级请求由模型决定：脚本化模型的真二进制测试覆盖了升级（`test_a_command_beyond_the_sandbox_asks_as_an_execution_and_runs_once_allowed`）与不升级（`test_a_command_that_writes_without_asking_for_more_fails_in_the_sandbox`）两条路径，真实模型在只读沙箱里是否会去申请没有验证 | 策略 `never` 加只读沙箱，只能读取；没有 broker 或 `allow_all` 时收到的任何请求都被拒绝并记 `codex_approval_refused` |

**ACP 启动参数**：ACP 通道的审批只由宿主策略回答。已知的 ACP Agent（Gemini、Qwen）按契约启动：`--acp --approval-mode default`，其后才是配置里的参数；配置里写的 `--yolo`、`-y`、`--approval-mode` 这类让 Agent 自己跳过询问的旗标（连同取值，`--flag=value` 写法也认）会被去掉，并记 WARNING `acp_approval_flags_dropped` 写明去掉了什么。显式写出 `--approval-mode default` 不是多余的。实测（Gemini 0.71.1、Qwen 0.28.6，隔离的 HOME 与工作区、假密钥、不调用模型）：只给 `--acp` 时，会话模式由 Agent 自己的设置决定——Gemini 设置里的 `general.defaultApprovalMode: auto_edit` 得到 `autoEdit`（Gemini 只在它信任的目录里认这条设置），Qwen 设置里的 `tools.approvalMode: yolo` 得到 `yolo`，Qwen 不设置时的默认是由模型判断的 `auto`——这些都是不问宿主就动手；带上这个旗标，不论设置怎么写都报告 `default`。`safe` 下运行时还核对 `session/new` 回答里的当前模式必须是契约的 `default`：不是就用 `session/set_mode` 改回并记 WARNING `acp_session_mode_reset`，Agent 没有这个模式或改不回来，回合以错误结束，不带着一个不问的 Agent 往下跑。不用 `plan`：那会把会询问的 Agent 变成只会拒绝的 Agent。以前的预设存下的 `--output-format stream-json` 不会让两者启动失败，存下的配置不需要迁移。登录被拒（`session/new` 以 ACP 的 `auth_required`，即错误码 -32000 回答）时，回合以 `AUTH_FAILED` 结束并带 Agent 自己的话。未知命令（认的是可执行文件名本身，名字里带 `claude` / `gemini` 的适配器或包装脚本也算未知）的参数原样启动。

`allow_all`：ACP 自动选 Agent 所列的 allow 选项；CLI 用各自的放行档位（Claude `bypassPermissions`）；Codex 为策略 `never` 加用户自己的 workspace-write 沙箱（`config/read` 读出的 `[sandbox_workspace_write]` 设置，没写就是 Codex 的默认），受沙箱约束而不问人，需要沙箱外权限的动作直接失败。ACP 的 Gemini / Qwen 在 `allow_all` 下仍以 `default` 启动，由宿主策略答应每一个请求，不再靠 `--approval-mode yolo`。

**文件回调说真话**（`runtime/acp/callback.py`）：宿主只向 Agent 提供 `fs/write_text_file`（`client_capabilities()` 在 `initialize` 里宣告 `fs.readTextFile: false`、`fs.writeTextFile: true`、`terminal: false`）。写只服务工作区内的文件，按字节写，所以 Agent 写下的就是落盘的（含换行符）；工作区外的路径、被拒绝的写入与失败都以 JSON-RPC 错误如实告诉 Agent；`safe` 下每个文件第一次写之前先问宿主，人在编辑审批里已经允许的文件不再问第二次。不提供读：Agent 与宿主共用同一块磁盘，宿主的读只是第二个读者，而且不是每个 Agent 都认得「读不到」这个错误——Gemini 0.71.1 靠读文件区分新文件与已有文件，把「资源不存在」当作失败，于是连新文件都建不了（实测见下）。

**Claude 权限询问的真二进制事实**（claude 2.1.330，`tests/toolkits/acp/runtime/cli/test_real_claude_permission_prompt.py`；真 `claude -p` 由脚本化的假 Anthropic API 驱动，不登录、不调用真实模型）：

- `approve` 返回一个文本块，内容是 `{"behavior":"allow","updatedInput":<input>}` 或 `{"behavior":"deny","message":…}`；拒绝的 `message` 以 `is_error: true` 的 `tool_result` 到达模型。
- Claude 自己在 60 秒后拒绝一条权限询问，没有任何答复时 300 秒后也会拒绝；只有 `--mcp-config` 里该服务器条目的 `timeout`（毫秒）能同时解除这两个上限（`MCP_TOOL_TIMEOUT` 环境变量只解除 60 秒，所以不用它）。桥接把它设为一天（`_PROMPT_TIMEOUT_MS`）：人要多久由宿主的 broker 定，回合结束时等待随之结束。
- 实测一次 310 秒后才答复的询问仍被等到并执行（该测试耗时 311.5 秒，超过项目的 300 秒默认超时，所以单独标了 420 秒）；一天这个取值本身没有被等满过。

**Gemini / Qwen 权限往返的真二进制事实**（gemini 0.71.1、qwen 0.28.6，`tests/toolkits/acp/runtime/acp/test_real_acp_permission_round_trip.py`；真实二进制由本机脚本化的假模型 API 驱动——Qwen 走 OpenAI chat.completions，Gemini 走 `streamGenerateContent`——隔离的 HOME、假密钥，不登录、不调用真实模型；Agent 以存下的 `type: cli` 注册，由契约放到 ACP 上）：

- 每个场景 Gemini 与 Qwen 各跑一次，18 个测试共 36 秒：拒绝 → 文件没有写出，Agent 把取消报给模型，回合正常结束；批准 → 新建的文件写出，已有文件先读后改（Qwen 的 `edit`、Gemini 的 `replace`）也写出；`allow_all` → 不问任何人；`safe` 且没人可问 → 写不出；shell 命令以 `execute` 询问，批准才执行；读文件不询问。
- 两个 Agent 的请求形状不同，宿主界面不能假定同形：Gemini 的 `kind` 是 `edit`、`input` 是空对象、标题是缩略的、`paths` 有值（macOS 上是解析后的 `/private/var/...`）；Qwen 的 `input` 带 `file_path` 与 `content`。
- 宿主宣告过读能力时，Gemini 创建新文件失败，权限请求根本没有发出，工具以「Error checking existing file: Resource not found」结束：Gemini 的 `AcpFileSystemService` 只把 `Error` 实例当作文件不存在，ACP 的 JS SDK 却用普通的 JSON-RPC 错误对象拒绝，宿主回答「资源不存在」于是被当作真正的失败。两种改法都验证过能建出文件：读不到时回空内容，或不宣告读能力；选了后者，因为空内容等于告诉 Agent 文件存在。
- 停止：Gemini 在等宿主回答一条权限请求时，只收到 `session/cancel` 并不结束提示——宿主没有回答，回合在取消 60 秒后仍未结束；宿主按 ACP 对取消的要求把等待中的请求答为 `cancelled` 之后，Gemini 2.3～2.6 秒结束，`DONE` 的 `stop_reason` 是 `cancelled`，文件没有写出，人那边的等待被撤回。Qwen 收到 `session/cancel` 就结束提示（宿主不回答也一样，去掉这个回答后 Qwen 的停止测试仍通过，Gemini 的不通过），回答之后 1.5 秒结束。

**Gemini / Qwen 会话续接的真二进制事实**（gemini 0.71.1、qwen 0.28.6，`tests/toolkits/acp/runtime/acp/test_real_acp_session_continuity.py`；同样由脚本化的假模型 API 驱动，证据是 Agent 发给模型的请求里还有上一个回合说过的话）：

- 握手声明各不相同：Gemini 只有 `loadSession`（没有 `sessionCapabilities`，对 `session/resume` 回「method not found」），Qwen 有 `loadSession` 与 `sessionCapabilities.list` / `resume`，opencode 另有 `fork` / `delete` / `additionalDirectories` / `close`。运行时只调用 Agent 声明过的方法，声明记在 `AgentCapabilities`，能力与使用它的代码一起进入。
- 续接：Qwen 经 `session/resume`，响应前没有历史重放；Gemini 经 `session/load`，响应前把整段历史（用户与 Agent 的消息）作为 `session/update` 重放。重放在 `begin_turn` 时被丢掉，回合输出里没有上一个回合的回复；新进程续接后发给模型的请求带着上一个回合的内容。
- 失效：Gemini 对不认识的会话回 -32603「Invalid session identifier」，Qwen 回 -32002；除 `auth_required`（-32000，登录问题，记录保留）外，宿主一律按「Agent 不认识这个会话」处理：开新会话、发出 `session_expired`、记录被新会话替换。
- 一个运行时对每个 harness 会话持有一个 ACP 会话；事件带的是宿主的会话 id，不是 Agent 的。Qwen 另会发出不是 ACP 方法名的厂商通知（`qwen/notify/…`），SDK 把它们记成 ERROR 日志（Method not found），不影响回合。
- `fork`、`list`、`additionalDirectories` 已在握手里读到，但没有使用它们的功能前不进 `AgentCapabilities`：分叉随会话/任务的 worktree 与 /fork，多目录随账号档案与工作区集合。

### 3.6 BackendDetector（后端自动检测）

**检测策略**：
1. `which` / `where` 命令
2. 扩展 PATH 搜索
3. 常见安装路径（/usr/local/bin, ~/.local/bin）
4. npm global
5. 可选版本检测（`--version`，按调用方需要开启）
6. 可选强制刷新（`refresh=True`，绕过缓存）

**返回**：`list[DetectedBackend]`（name, path, version）
**缓存**：进程级双缓存（带版本 / 不带版本）+ 软 TTL（默认 300s），避免重复路径扫描与重复版本探测，同时避免长期陈旧结果常驻

### 3.7 RuntimePool

**价值**：统一管理任意 `RuntimeBackend` 实例，配置驱动 + 并发控制（`asyncio.Semaphore`）。

```
RuntimePool(*, max_concurrent: int = 4, cli_session_store: CliSessionStore | None = None)
├── register(name, config: RuntimeConfig) -> None
├── get(name, workspace) -> RuntimeBackend  # 按（名字, 工作区）惰性创建的共享后端
├── prompt(name, task, *, workspace, mode) -> str   # 委托 run_turn()，收集 text_delta
├── run_turn(name, prompt, session_id, *, workspace, mode, mcp_servers, images, permissions) -> AsyncGenerator[RuntimeEvent]  # Semaphore 并发控制
├── cancel(name, session_id) -> None        # 级联取消 → 正在跑该会话回合的后端的 backend.cancel()；没有在跑的回合时什么也不做
├── available_backends -> list[str]
├── release_resident() -> None              # 只释放常驻进程型（ACP、app server）后端，保留 CLI 后端及其 --resume 状态
└── close_all() -> None                     # 关闭所有后端（含在途 oneshot）
```

**要点**：
- `RuntimeConfig.backend_type` 选择 `AcpRuntime` / `CliRuntime` / `CodexRuntime`
- `max_concurrent` 限制同时执行的委托任务数，避免进程与文件句柄耗尽
- `prompt()` 委托给 `run_turn()`，并发控制统一在 `run_turn()` 层
- **回合的工作区 `workspace`**：调用方给每个回合一个启动目录（绝对路径，由 `types.normalize_workspace` 规整，相对路径当场 `ValueError`）；池、运行时与委派工具都不从进程目录或环境推断它，也不把它写进提示词。后端按 `(名字, 工作区)` 创建，并在整个生命周期内固定在该目录：同一个 agent 在两个目录里各有自己的后端、会话与进程，互不串用。`RuntimeConfig` 描述 agent 是谁，不带目录
- **会话模式 `mode`**：`persistent`（默认）每个（后端名, 工作区）共享一个后端实例，会话跨回合延续（CLI 的 `--resume` id、ACP 的常驻进程与会话）；`oneshot` 在**私有后端实例**上运行、回合结束即关闭（关闭失败只记 warning，不覆盖回合结果或回合异常）——总是全新会话，且不触碰 persistent 后端的会话与进程。在跑的回合按 `(name, session_id)` 登记，`cancel` 只作用于登记里的回合（还在等后端的回合没有可停的东西，什么也不做），`close_all` 把登记里的私有后端一并关闭；同一（名字, 工作区）的回合由 per-backend 锁串行化（避免同一工作区同时跑两个同名 agent）
- **空闲释放 `release_resident()`**：宿主在会话空闲时调用，只关闭并摘除常驻进程型（`backend_type` 为 `acp` 或 `app_server`）后端，下一回合惰性重建（Codex 的对话在有 `cli_session_store` 时从它的 rollout 续接）；CLI 后端回合之间没有进程，只持有 `--resume` 会话 id，原样保留，所以空闲回收不会让 CLI 会话断档。调用方须保证此刻池上没有在跑的回合（与 `close_all()` 同一约定）
- **CLI 会话持久化 `cli_session_store`**：宿主可注入 `CliSessionStore`（`load` / `save`，键为 harness session id，一个 store 服务一个会话作用域），让 CLI 后端的 `--resume` id 与 Codex 的线程 id 跨宿主进程重启存活。只注入共享的 persistent 后端；oneshot 私有后端是回合结束即消失的全新会话，既不读也不写 store。store 的任何异常只记 warning 并降级（丢连续性，不丢回合）；harness 在回合收尾处直接 await store 且不设超时，调用时延由实现方自己约束；harness 本身不依赖任何存储实现
- **进程生命周期**：不做周期性存活巡检。`CliRuntime` 每回合一个进程，回合之间没有进程是正常空闲态；`AcpRuntime` 与 `CodexRuntime` 的常驻进程退出后，下一次 `run_turn` 发现 `is_alive` 为假就重新拉起进程（`_ensure_connection`）。进程是否卡住不属于存活检测，由 `timeout_seconds` 兜底；各回合静默期间 `CliRuntime`、`AcpRuntime` 与 `CodexRuntime` 每 15 s 发一个 `working` 状态（见 §6 “静默心跳”），宿主的空闲看门狗据此知道它还活着
- **委托窄协议 `DelegationPool`（`types.py`）**：委托调用方只依赖 `available_backends` / `get_config` / `run_turn`（必带 `workspace`）/ `cancel`；`RuntimePool` 与宿主侧包装器（如按会话登记在途回合的 facade）都满足它，`create_invoke_acp_agent_tool` 接受它
- **权限 `permissions`**：`run_turn(permissions=broker)` 把一个 `PermissionBroker` 交给本回合，由 `TurnPermissions` 包好后传给后端；只有声明 `supports_permission_requests` 的后端会问它，其余后端与没有 broker 的回合里，`safe` 的 Agent 只能读取
- **MCP 注入能力守卫**：`run_turn()` 在转发 `mcp_servers` 前检查 `backend.capabilities.supports_mcp`，不支持的 backends（如 `CliRuntime`）收到 `None` 而非原始配置，杜绝向不支持 MCP 注入的后端传递配置引发会话崩溃；跳过时记录 `pool_mcp_skipped` WARNING 便于观测，回合以一条 `mcp_not_attached` status_update（`servers`：未交出的 Server 名）开头，宿主据此如实告诉用户 agent 没有拿到这些 Server，而不是把缺了它们的回答当成有它们的回答
- **图片能力守卫**：`run_turn(images=[本地图片文件…])` 与 MCP 同形——只有声明 `supports_images` 的后端才收到文件列表，其余收到 `None`（记 `pool_images_skipped` INFO，不是告警：宿主提示词里仍有图片路径一行作为兜底；回合以一条 `images_not_sent` status_update 开头，让宿主能如实告诉用户这张图没有直接交给 agent）；直接调用 `CliRuntime` 传入它不接收的图片会记 `cli_runtime_images_ignored` WARNING；`AcpRuntime` 面对没有声明图片提示能力的 agent，只发文本块并记 `acp_runtime_images_skipped` INFO，回合以同一条 `images_not_sent` status_update 开头

### 3.8 RuntimeConfig（统一配置）

```
RuntimeConfig (frozen dataclass)
├── backend_type: "acp" | "cli" | "app_server"
├── command: str | None                  # CLI/ACP/app-server 的可执行文件路径
├── args: list[str]                      # 命令行参数
├── env: dict[str, str] | None           # 额外环境变量
├── timeout_seconds: int = 300           # 整个回合的时间上限（含 CLI 静默的时间），由宿主按场景设置
├── permission_mode: PermissionMode      # 权限模式
├── auth_mode: AuthMode = "subscription" # 鉴权模式：订阅登录态 / API Key
├── mcp_servers: Sequence[McpServerConfig] # 交给 agent 的 MCP Server（stdio 或 HTTP）
├── max_response_chars: int = 50_000     # 响应截断限制
├── max_turns: int = 25                  # 轮次上限：有原生上限的 CLI（Claude）经 --max-turns 自行停止，其余后端由 delegate 按工具调用数兜底
└── description: str = ""                # Agent 能力描述（注入 tool description）
```

**环境变量管理 + 鉴权模式（`auth_mode`）**：
- 环境基线：`build_safe_env()` 只让子进程继承核心变量（PATH、HOME、语言、代理等，复用 `core/security/process_env/env_isolation.py` 的 `CORE` 策略）加上该 CLI 自己的登录目录变量（`CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `GEMINI_CLI_HOME` / `QWEN_HOME`，取自 auth profile 的 `relocation_vars`）；宿主的 provider 密钥与其余环境一律不带——委托 CLI 会把自己的环境原样交给它执行的每条命令。后端另需的变量（`JAVA_HOME`、`SSH_AUTH_SOCK`、云厂商路由变量等）经 `RuntimeConfig.env` 设置。`CliRuntime`、`AcpRuntime` 与 `CodexRuntime` 启动前统一调用；「企业网络兼容」打开时它还在末尾补上 `NODE_USE_SYSTEM_CA=1`（`infra/tls_compat.py::with_node_system_ca`），让基于 Node 的 CLI 像宿主一样信任操作系统证书库，`RuntimeConfig.env` 自己写了该变量则保留它的值；受管工具链下载的 Node 是认该变量的 `NODE_VERSION`，其 npm 安装子进程同样带它
- 账号（`RuntimeConfig.account_home`）：宿主把已知 Agent 跑在命名账号的目录里时，`build_safe_env` 在其余一切（含 `RuntimeConfig.env`）之后写入把该 CLI 搬进那个目录的变量（`launch_env.account_env`；各 CLI 怎么被搬由 auth profile 的契约给出）。必须最后写：Gemini、Qwen 的家目录变量（`GEMINI_CLI_HOME`、`QWEN_HOME`）带 provider 前缀，订阅模式会把 `env` 里这类条目当作密钥丢弃。`account_home` 必须是绝对路径且命令必须是已知 Agent，否则 `RuntimePool.register` 当场抛 `ValueError`，不留到首个回合才失败
- Claude 默认环境：`claude.ENV_DEFAULTS` 垫在宿主与配置环境之下（后两者可覆盖）；Claude 的 `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` 让无头回合不留后台任务（后台任务期间 `claude -p` 不会退出）
- `subscription`（默认）：剥离所有 provider 密钥 **并丢弃** `env` 中注入的密钥，强制委托 CLI 走自身订阅登录态（用户的 ChatGPT Plus / Claude Max / Gemini 订阅），绝不静默回退按量计费的 API Key
- `api_key`：业务层经 `env` 注入该后端应计费的单一 provider 密钥，剥离基线后重新应用；Codex app server 不从环境读密钥，所以 `CodexRuntime` 用 `account/login/start` 把密钥交给 server（只在内存里），并让 Codex 以 `-c cli_auth_credentials_store="ephemeral"` 启动，密钥不进子进程环境（它会被 Codex 执行的每条命令继承）、不写进 Codex 的 home，用户自己的登录文件原样不动（`test_an_api_key_logs_in_through_the_server_and_leaves_the_users_own_login_alone`）
- 价值：让用户在自己的机器上用已购订阅驱动外部 CLI，无需重复购买 API Key；托管部署不保存订阅登录，只用 API Key（详见 3.10 auth 子系统）

### 3.9 _base.py（RuntimeBackend 基类）

**价值**：提取各 Runtime 的通用逻辑，避免重复代码。

**通用功能**：
- 环境变量清理（`build_safe_env`）
- 超时控制（`asyncio.timeout`）；回合生成器由 `aclosing` 收尾，超时、异常或消费方提前放弃都会停掉外部进程
- 流式响应截断（`max_response_chars`，统一对 TEXT_DELTA 事件逐字符计数）
- 日志规范化

### 3.10 auth 子系统（订阅鉴权）

**价值**：让委托的外部 CLI（Codex / Claude Code / Gemini / Qwen）运行在**用户自己的模型订阅**上而非按量计费的 API Key。框架只出**机制**（业务无关），由业务层驱动登录、状态徽章与登出，并判定当前部署是否允许保留订阅登录。宿主从不持有凭据：登录由 CLI 自己完成并保存，这里没有任何接受凭据再存下来的入口。

```
auth/
├── _profiles.py        # AuthProfile：每个 CLI 的凭据路径、登录/登出/查询登录态的命令、谁来跑登录（LoginStrategy）、本机与远程浏览器各自的登录流程、api_key env
│                       #   环境感知 resolve_home（CODEX_HOME/CLAUDE_CONFIG_DIR → /persistent）
├── accounts.py         # AccountStore：同一个 CLI 的多个登录各占一个目录（命名账号 + 按 API Key 计费运行的稳定目录）
├── credential_store.py # CredentialStore：状态检测 / 清除（从不写入凭据）
├── login_session.py    # CliLoginSession：驱动 BROWSER_OAUTH 类 CLI（Codex）登录，流式 AuthEvent（提取链接/设备码），以 CLI 自报的登录态判定成败
├── terminal_login.py   # terminal_login：用户在终端里敲的官方登录命令（账号隔离变量、CLI 的路径与托管 Node），按 POSIX / PowerShell / cmd.exe 各写一份，不启动任何进程
└── logout.py           # log_out：先让 CLI 自己登出，再清凭据文件，返回 CLI 事后报告的登录态
```

**机制**：
- **登录态检测**（`resolve_login_state`）：该后端是否已登录？有状态命令的 CLI（`claude auth status`、`codex login status`）问 CLI 自己并以它的回答为准，没有的（gemini、qwen）与 CLI 无回应时退回 `CredentialStore.state` 的文件判断；驱动状态徽章；鉴权态从不缓存（用户随时登录/登出）
- **宿主驱动的登录**（`CliLoginSession`，`LoginStrategy.BROWSER_OAUTH`，目前只有 Codex）：spawn `<cli> <login_command(remote)>`，并发读 stdout/stderr，去掉色彩转义后把链接/设备码变成可操作 PROMPT 事件（CLI 自己的回调地址不当链接给用户）；进程干净退出且 CLI 自报已登录才算 SUCCESS（登录放在钥匙串里的 CLI 不留凭据文件，文件不能当裁判），失败以 CLI 最后一句话收尾；Codex 本机浏览器走 `login`、远程浏览器走 `login --device-auth`
- **终端登录**（`terminal_login`，`LoginStrategy.TERMINAL`：Claude、Gemini、Qwen）：`claude auth login --claudeai` 打印链接并向用户要页面上的验证码，Gemini 与 Qwen 没有登录命令（界面首次启动时才问）；由宿主驱动就得让用户的验证码与登录经过宿主，所以改由用户在终端里跑 CLI 自己的登录，宿主只描述这条命令：CLI 按终端会找到它的方式写（终端按名字能找到的就是这个文件时写名字，否则写路径，由隔离工具链装的 CLI 同时把托管的 Node 放到 `PATH` 前面）、把登录留在账号目录里的变量（`isolated_env`），按 POSIX、PowerShell、cmd.exe 各写一份（cmd.exe 写不出的路径，该写法不给）；之后再问 CLI 自己谁登录了
- **登出**（`log_out`）：删凭据文件够不着放在钥匙串里的登录，所以先让 CLI 自己登出（`claude auth logout`、`codex logout`，带该 home 的环境、不带运营方的 provider 密钥），再清文件，最后返回 CLI 事后自报的登录态；真二进制契约（`test_real_cli_login_flow.py`）盯住各登录命令（含用户在终端里敲的那条）仍被 CLI 接受、各 CLI 自己的登出让 home 不再有登录
- **命名账号**（`AccountStore`）：同一个 CLI 的两个订阅登录（工作号、个人号）不能共用 CLI 存登录的那个目录。账号就是宿主在自选根目录下按名字建的目录 `<根>/<后端>/<名字>`，目录树本身是登记表（不存第二份记录，也不跟随符号链接）；CLI 被重定向进去后只看得到该账号的登录、设置与历史，用户自己 home 里的登录不被碰。各 CLI 的搬法是它契约的一部分（`AuthProfile.isolated_env`）：Claude `CLAUDE_CONFIG_DIR`、Codex `CODEX_HOME`、Qwen `QWEN_HOME`、Gemini `GEMINI_CLI_HOME`（状态在 `<根>/.gemini` 下）并加 `GEMINI_FORCE_FILE_STORAGE=true`（否则 Gemini 把凭据放进所有 home 共用的系统钥匙串，账号会悄悄共用一个登录）。`AccountStore.api_key_home` 给按 API Key 计费的运行一个从不被登录、不是账号、不被列出的稳定目录，遗留的订阅登录因此不会被读到。会话与账号的绑定不另存：Agent 换账号后先前的会话 id 在新账号里不存在，走既有的“会话失效”重试开新会话并提示。真二进制契约（`test_real_cli_account_home.py`）逐个 CLI 盯住：作为账号跑一回合，写下的一切都在账号目录里（进程自己的 home 保持为空）、对话只在该账号里续接、一个账号里的登录只是该账号的登录
- **不接受凭据**：`CredentialStore` 只检测与清除，没有写入口；订阅登录由 CLI 自己落盘，harness 无法拦截，业务层必须在不属于用户的宿主（托管沙箱）上于启动 `CliLoginSession`、描述终端登录之前拒绝，检测与清除照常可用，使留下的登录仍能被看到并移除

**设计铁律**：
- 鉴权态**绝不进入** Turn1 工具描述；`create_invoke_acp_agent_tool` 使用**固定 schema**（backend 列表仅在运行时 KeyError 返回值中暴露），不破坏提示词前缀缓存
- 凭据路径环境感知：宿主通过重定向 home（如 `CODEX_HOME` → 持久卷）即可持久化凭据，harness 不依赖部署形态
- 登录态以 CLI 自述为准（文件判断看不见 macOS Keychain 里的 Claude 登录，也会把 CLI 已不认的文件当作已登录），但仍**不做运行时硬预检**：CLI 自身的未登录错误以 `AUTH_FAILED`（Claude 的 `authentication_failed`、Codex 的 401）带着 CLI 原话准确透传，状态可见性由徽章承担。两个 CLI 的自述格式由真二进制契约测试（`test_real_cli_login_contract.py`）盯住：claude 在 stdout 打 JSON（`loggedIn`、`authMethod`，未登录时退出码为 1），codex 在 stderr 说话（退出码 0 / 1）

---

## 4. 文件结构（与代码库一致）

```
acp/
├── __init__.py             # 惰性导出 RuntimePool / RuntimeConfig / RuntimeBackend + Server
├── __main__.py             # CLI 入口
├── acp_agent_tools.py      # delegate 工具门面（业务层委托入口）
├── delegate_report.py      # 一次委托回合的报告：用量与计数、只带标题的通知步骤、Agent 提前结束时给调用模型的注记
├── mcp_servers.py          # 宿主交给 agent 的 MCP Server：StdioMcpServer、HttpMcpServer（名称与 URL 校验，env / headers 不进 repr），read_only 声明只读
├── types.py                # RuntimeEvent、Protocol（含 CliSessionStore）、RuntimeConfig、CliSessionRecord、错误码等
├── ACP_SYSTEM.md           # 本设计文档
├── _ARCH.md                # 模块索引
├── core/                   # 共享基础设施
│   ├── __init__.py
│   ├── probe.py            # run_probe：向 CLI 提一个短问题（--version、auth status），精确环境、输出与时间有界，超时杀整棵进程树
│   └── backend_detector.py # CLI 探测（detect + detect_with_auth 安装+登录态一次性视图；版本与登录态并发询问）
├── auth/                   # 订阅鉴权子系统（业务无关机制）
│   ├── __init__.py         # 公共导出
│   ├── _profiles.py        # AuthProfile：CLI 凭据路径/登录命令/策略/家目录重定位变量
│   ├── accounts.py         # AccountStore：命名账号（每个登录一个目录）与按 API Key 计费运行的稳定目录
│   ├── credential_store.py # 状态检测 + 清除
│   ├── launch_env.py       # cli_base_env：每次启动 CLI 的基线环境（回合与探测共用）；account_env：把 CLI 搬进账号目录的变量
│   ├── login_state.py      # probe_login / resolve_login_state：CLI 自述的登录态，文件判断作兜底
│   ├── login_session.py    # CliLoginSession：驱动 <cli> login，流式 AuthEvent
│   └── terminal_login.py   # terminal_login：用户在终端里敲的登录命令，三种 shell 的写法
├── server/                 # ACP Server 方向（IDE → Agent）
│   ├── __init__.py
│   ├── server.py           # ACP Server（acp.Agent，支持动态绑定宿主 MCP 工具）
│   ├── bridge.py           # Session ↔ 宿主 Agent（会话生命周期与多工厂适配）
│   ├── mcp_converter.py    # ACP MCP Schema → Myrm MCPConfig 协议适配层
│   └── event_translator.py # AgentEvent → SessionNotification
├── runtime/                # Runtime 方向（Agent → 外部 Agent）
│   ├── __init__.py
│   ├── _base.py            # BaseRuntime、build_safe_env、各运行时共用的静默心跳（KEEPALIVE_SECONDS / working_status）与连续 401 重试上限 UNAUTHORIZED_RETRIES_LIMIT
│   ├── event_text.py       # 各运行时共用的文本：工具调用摘要、失败文本整理 condense_failure_text、消息之间的段落分隔 paragraph_break
│   ├── process.py          # ManagedProcess / spawn_managed_process：逐行 stdout（静默满一个周期产出 None 供心跳）、有界 stderr 尾部、唯一的停止入口（SIGTERM → SIGKILL）；程序不存在抛 ProcessLaunchError
│   ├── session_book.py     # 会话记录簿 CliSessionBook（Claude 会话 id、Codex 线程 id、ACP 会话 id；内存优先 + 写穿到宿主 CliSessionStore）
│   ├── image_blocks.py     # 回合图片文件 → base64 块（ImageBlock）：按文件头判定格式，读不了或不是图片的略去并记 WARNING；各运行时共用
│   ├── pool.py             # RuntimePool
│   ├── turn_permissions.py # TurnPermissions：回合内的权限请求守卫（broker 出错按拒绝、等人时暂停时间上限、回合结束撤销等待）
│   ├── acp/                # ACP 后端子包
│   │   ├── __init__.py
│   │   ├── runtime.py      # AcpRuntime：ACP 子进程 + JSON-RPC（静默心跳；ACP SDK 缺失 → BACKEND_NOT_FOUND）
│   │   ├── capabilities.py # AgentCapabilities：Agent 在 initialize 里声明的能力（图片、HTTP MCP、load、resume、close），运行时只按它决定调用什么
│   │   ├── sessions.py     # AgentSessions：harness 会话对应的 ACP 会话、续接（resume 优先于 load）、失效与工作目录变化
│   │   ├── callback.py     # ACP 回调处理器（会话更新、工作区内按字节读写的文件回调；权限请求交给 permission_policy）
│   │   ├── mcp_origin.py   # mcp_server_of：agent 在权限请求的 _meta 里说一次工具调用属于宿主交给它的哪个 MCP Server
│   │   ├── permission_policy.py # AcpPermissionPolicy：`safe` 下放行读类动作与宿主声明只读的 MCP Server 的工具、其余交给 broker，记住人同意过的文件
│   │   └── stalled_agent.py # 对不再读 stdin 的 agent 有界停止：5 s 后结束 agent 及它启动的全部进程
│   ├── codex/              # Codex app-server 后端子包
│   │   ├── __init__.py
│   │   ├── runtime.py      # CodexRuntime：常驻 server、线程与回合、停止与失败收尾、静默心跳、401 快速失败
│   │   ├── connection.py   # 一个已启动并握过手的 server 进程（环境、握手与版本下限检查、API 密钥的内存登录、停止）
│   │   ├── rpc.py          # JSON-RPC 对端：请求、通知与 server 发来的请求（请求参数从不进日志）
│   │   ├── protocol.py     # 宿主说的话：握手、版本下限、回合策略（权限模式 → 审批策略与沙箱）、开线程（含必须起得来的宿主 MCP Server）与跑回合的请求
│   │   ├── turn_events.py  # 一个回合的通知 → RuntimeEvent（纯翻译，无 I/O）
│   │   ├── approvals.py    # server 的审批请求 → PermissionRequest → 答复
│   │   ├── threads.py      # 线程簿：会话对应的线程、续接、失效与工作目录变化
│   │   ├── items.py        # 回合内已见的条目（补丁内容供审批与事件共用）
│   │   ├── usage.py        # 线程累计用量 → 每步增量
│   │   ├── failure.py      # 由结构化 codexErrorInfo 归类失败
│   │   └── wire.py         # 对 server 发来的 JSON 的类型化读取
│   └── cli/                # CLI 后端子包
│       ├── __init__.py
│       ├── claude.py       # Claude Code 的命令行（续接语法、原生轮次上限、增量文本、图片传递、宿主 MCP Server 的配置文件、权限桥、默认环境变量）与会话 id 形态校验；is_claude 按程序名认出它
│       ├── failure.py      # 失败分类：用 CLI 自己的话（result 行 / 退出码）给出错误码
│       ├── parser.py       # 各输出格式共享的事件构造（工具调用/结果、usage、thinking）与 error 行的原文 error_text
│       ├── permission_bridge.py # PermissionBridge：Claude 权限询问的本机回环 MCP 端点（每回合一个，随机 token）
│       ├── runtime.py      # CliRuntime：启动、续接、失效重试、会话 id 落盘、静默心跳、401 快速失败、回合收尾
│       └── turn_parser.py  # 单回合 NDJSON 解析器（announce / result / 顶层 error 通知 / 连续 401 重试计数 / 增量文本去重）
└── toolchains/             # 隔离工具链（Node/npm）
    ├── __init__.py
    └── manager.py          # IsolatedToolchainManager：安装要么产出能启动的 CLI 要么抛 ToolchainError；内核锁、有界下载与 npm、跟随用户 npm 配置
```

`toolkits/acp/acp_agent_tools.py` 通过 `RuntimePool` 发起委托，汇总 `USAGE_UPDATE` 事件的 token 消耗并推送至前端；用户停止时经 `utils/runtime/cancel_hook.on_token_cancelled` 停掉外部进程。`runtime/cli/turn_parser.py` 把一个回合的 NDJSON 行解析为 `RuntimeEvent`：Claude stream-json（content block 里的文本与 tool_use / tool_result，`--include-partial-messages` 的 `stream_event` 增量文本并按 API message id 去重）、一行一个事件的通用格式与纯文本；同时暴露 CLI 最先 announce 的会话 id 与 CLI 自己的 `result` 结论。`runtime/cli/parser.py` 提供各格式共用的事件构造。Codex 不经这条路：`runtime/codex/turn_events.TurnTranslator` 把 app server 的通知（`item/started`、`item/completed`、`item/agentMessage/delta`、`thread/tokenUsage/updated`、`turn/completed` 等）翻成同一套 `RuntimeEvent`，只翻译当前回合的通知；一条新消息接在前文之后时以空行隔开，已经流式发出的条目完成时不重复。

---

## 5. 能力清单

| 能力 | 实现 |
|------|------|
| 可插拔后端 | `RuntimeBackend` Protocol：ACP 与 CLI 两种实现，带 `cancel()` / `resume()` / `get_info()`（§3.1） |
| 权限管理 | 2 种模式：`safe`（能问的 Agent 逐条问人，不能问的只读）/ `allow_all`（§3.5），宿主经 `PermissionBroker` 回答（§3.4） |
| 会话 | `persistent` / `oneshot` 两种会话模式；CLI 的 `--resume` id 可经 `CliSessionStore` 跨宿主进程重启保留（§3.7） |
| 后端检测 | 路径检测 + 可选版本检测，进程级缓存（§3.6） |
| 事件类型 | 8 种（含 `status_update`）（§3.2） |
| 错误处理 | 结构化错误码 + `retryable`（§3.3） |
| 安全模型 | 环境清理、白名单、工作目录内 FS 回调 |
| MCP 集成 | Session MCP 注入，受 `supports_mcp` 能力守卫约束（§3.7） |

---

## 6. 框架层/业务层边界

> 遵循 FRAMEWORK_DESIGN_PRINCIPLES.md：框架提供 Protocol + 默认实现，业务层按需替换。

### 框架层提供（开箱即用）

| 模块 | 类型 | 说明 |
|------|------|------|
| types.py | 核心类型 | Protocol / dataclass / StrEnum 定义 |
| runtime/_base.py | 基类 | 环境清理、超时、截断 = 自我保护 |
| runtime/acp/runtime.py | 实现 | ACP 协议后端 |
| runtime/cli/runtime.py | 实现 | CLI spawn 后端 |
| runtime/pool.py | 管理器 | 单实例内多后端管理 |
| runtime/turn_permissions.py | 守卫 | 回合内的权限请求：broker 出错按拒绝、等人时暂停时间上限、回合结束撤销等待 |
| runtime/acp/permission_policy.py | 实现 | ACP 后端 `safe` 的裁决：读类放行、其余交给 broker |
| runtime/cli/permission_bridge.py | 实现 | Claude CLI 的权限询问端点 |
| core/backend_detector.py | 工具 | 后端自动检测 |

### 业务层自定义（通过 Protocol 注入）

| 能力 | 框架提供的扩展点 | 业务层实现示例 |
|------|----------------|-------------|
| 权限交互 UI | `PermissionBroker` Protocol（`run_turn(permissions=…)`） | `decide(request)` 里把请求展示给人，返回 `PermissionVerdict`（myrm-agent-server 的 `ChatPermissionBroker`：每条请求是该聊天里的一条 `external_agent_permission` 审批） |
| 监控集成 | `run_turn` 的事件流 | 逐事件消费并推送到 Prometheus / DataDog |
| 自定义后端 | RuntimeBackend Protocol | HTTP API Agent / 自研 Agent |

### 业务层接入（myrm-agent-server）

三层架构将框架能力接入到实际产品：

1. **配置层**：`UserConfig` 表 `config_key='externalAgents'`，JSON 格式 `{"agents": [{name, type, command, args, enabled, ...}]}`
2. **注入层**：`GeneralAgent._setup_external_agents()` 从配置创建 `RuntimePool` + `invoke_acp_agent_tool` 工具
3. **执行层**：LLM 自主决定是否使用 `invoke_acp_agent_tool` 委托任务给外部 Agent

**增强功能**：
- **本地模式自动发现**：本地部署时自动探测 claude/codex/gemini/qwen，每个都带它的契约规定的通道（`type`）
- **配置校验**：无效配置跳过并记录日志，不阻断 Agent 启动
- **权限配置**：每个外部 Agent 可独立配置 `permissionMode`（`safe` 或 `allow_all`）；`safe` 下 Agent 的每次改动都是该聊天里的一条 `external_agent_permission` 审批（WebUI 抽屉、IM 卡片与 REST、批量、过期清理走同一个汇合点），600 秒无人答复按拒绝，回合结束或服务重启时遗留的请求被撤回
- **多渠道支持**：Web Chat 和 Channel 路径均支持外部 Agent
- **容错处理**：外部 Agent 配置失败不影响主 Agent 功能
- **前端名称唯一性校验**：保存时检测重复 Agent 名称，防止配置冲突
- **命令可用性测试**（本地模式）：通过 `POST /channels/manage/external-agents/test` 检测命令是否存在并返回版本信息
- **流式事件消费**：`invoke_acp_agent_tool` 通过 `pool.run_turn()` 逐事件消费，实时日志输出工具调用和状态更新，返回结果包含 tool_calls 计数等元数据
- **前端实时进度推送**：通过 ContextVar + `ToolProgressSink` 机制，`invoke_acp_agent_tool` 在执行期间将外部 Agent 的工具调用和状态事件实时推送至前端 SSE 流（复用 `TASKS_STEPS` 事件类型；会话连续性通知是只带标题的已完成步骤，措辞由前端按 `step_key` 本地化）
- **前端预设模板**：新增模式下提供 Claude Code / Codex CLI / Gemini CLI / Qwen Code 一键填充，降低配置门槛；命令恰好是已知 Agent 时，设置页按服务端 `/auth/status` 报告的通道只读显示类型，保存时存契约的通道而不是旧值
- **Max Turns 双层安全护栏**：Layer 1 是 CLI 自己的轮次上限——`claude.with_max_turns` 为 Claude 追加 `--max-turns`，到限后 CLI 自行结束并在 `result` 行（`error_max_turns` / `terminal_reason: max_turns`）报告，`CliRuntime` 不把它当故障，回合以 `DONE` 的 `stop_reason=max_turn_requests` 收尾——ACP Agent 自己的回合上限以同一个 `DONE` 到达，工具路径与直连路径都按「已达轮次上限」收尾（见 3.2 的 `StopReason`）；Layer 2 是 delegate 工具对 `TOOL_START` 的计数，只用于没有原生上限的后端（Claude 之外的一切，工具路径里的 `native_limit`），超限时 `pool.cancel()`——Claude 的一个 agent 轮次可发出多个工具调用，对它计数会提前截断。实测（真 claude 2.1.330，脚本化的假 Anthropic API）：`--max-turns 1` 的回合在第一轮工具结果之后以 `result` 行 `subtype: error_max_turns`、`terminal_reason: max_turns` 结束；`max_tokens` 与 `refusal` 两类停止由 Claude 自己重试后以 `is_error: true`、`terminal_reason: api_error` 的 `result` 报告，文字是 CLI 的原话（「API Error: …」），所以它们本来就以 `UNKNOWN` 错误原文呈现，不再另设映射；`tests/toolkits/acp/runtime/cli/test_claude_transcripts.py` 用录制的转录守这些形态，`test_real_claude_turn_end.py` 用真二进制守回合收尾。**回合内文字分段**：Claude 把工具调用前后的文字作为不同消息流出、之间什么都没有，工具调用不内联显示时读成一句连着的话；`TurnParser` 在工具调用之后的第一段文字前补空行（`event_text.paragraph_break` 数已有的换行，回合最开头不补，没有工具调用隔开的两条消息不动；ACP 路径同样用它，见 `acp/callback.py`）
- **稳定 Tool Schema**：`invoke_acp_agent_tool` 使用固定 tool description（不含动态 agent 列表），保护 Turn1 Prompt Cache；`agent_name` 无效时错误响应列出 `available_backends`
- **Deploy 门控（Server）**：`external_cli_deploy.is_external_cli_deploy_supported()` + profile `strip_deploy_incompatible_builtin_tools`；沙箱自动剔除 `external_cli`；BuiltinToolsPanel sandbox 硬禁用 toggle
- **MCP 会话注入**：`RuntimePool.run_turn` → `AcpRuntime.new_session(mcp_servers=…)`，Codex 经 `thread/start` 的 `config.mcp_servers`（无 MCP 时 Server 不传参数（None）；`RuntimeConfig.mcp_servers` 为配置源；注入前经 `capabilities.supports_mcp` 能力守卫，跳过不支持的 backends 并记录 `pool_mcp_skipped`；Server 的类型与各运行时的交付形态见下一条）
- **宿主 MCP Server（类型化 API）**：`mcp_servers.py` 的 `StdioMcpServer`（agent 自己启动的程序）与 `HttpMcpServer`（agent 带 header 以 streamable HTTP 连接的端点）。名称只能是字母数字以单个 `-` / `_` 相连的 1–64 个字符（agent 用它拼出 `mcp__<名称>__<工具>`，名称里不能有 `__`，否则名称在哪里结束就说不清），URL 必须是带主机的 http(s) 地址；`env` 与 `headers` 不进 `repr`，URL 不进错误消息，日志里不泄露凭据。`read_only=True` 表示这个 Server 的工具只读，`safe` 模式下不必逐次询问，如同读文件。各运行时按各自 agent 能接受的形态交付：**ACP**（Gemini、Qwen）经 `session/new` 的 `mcpServers`，HTTP Server 只交给声明了 `mcpCapabilities.http` 的 agent，否则不交，并让**每个回合**以 `mcp_not_attached`（`servers`：未交出的 Server 名）开头；**Claude** 把 Server 写进 `0600`、回合结束即删的 `--mcp-config` 临时文件（凭据不进 argv），`read_only` 的追加 `--allowedTools mcp__<名称>`；**Codex** 写进 `thread/start` 的 `config.mcp_servers`（`{command, args, env}` 或 `{url, http_headers}`，一律带 `required = true`：Codex 默认在后台启动 MCP Server，回合可能抢在它启动完成之前向模型发出请求，模型这一回合就看不到它的工具，启动失败也没有人被告知；`required` 让线程打开时等它，起不来就不开线程，错误带着 Server 名与它自己的原话，真二进制测试用一个起不来的 Server 守着：回合以一条点名该 Server 的错误结束，模型一个请求也没发；`read_only` 的加 `default_tools_approval_mode = "approve"`，否则 `on-request` 下 Codex 每次调用前都问）；其余 CLI 与不声明 `supports_mcp` 的后端不交，同样以 `mcp_not_attached` 告知。ACP agent 没有预先批准的机制，所以 `AcpPermissionPolicy` 在 `safe` 模式下放行 agent自己说属于本回合 `read_only` Server 的请求（`runtime/acp/mcp_origin.py`：Gemini 在 `_meta["gemini-cli"]["mcp"]["server_name"]` 给出 Server 名，Qwen 在 `_meta["toolName"]` 给出 `mcp__<server>__<tool>`；别的 agent、agent 自己的工具与没声明的 Server 一律照旧交给 broker）。真二进制事实（`test_real_http_mcp.py`：脚本化的假模型 + 记录每个请求的真 streamable-HTTP 端点，离线）：Claude 2.1.330、Codex 0.172.0、Gemini 0.71.1、Qwen 0.28.6 都带着 Bearer 连上并调用了工具，只读的不问、否则问；Claude 默认把 MCP 工具的结构延后到 `ToolSearch`（脚本化模型不搜索，测试里设 `ENABLE_TOOL_SEARCH=false`）；Gemini 在无头 ACP 里对未受信任的工作区**一个 MCP Server 也不连**（需 `GEMINI_CLI_TRUST_WORKSPACE=true`；宿主不得自动设置它：仓库自带的设置与钩子会借此绕过宿主的 broker），所以 Server 是否已被连上要以服务端见到的连接为准，不能假设；Qwen 在 `session/new` 返回之后才开始连接（约 0.15 s），给远程 Server 5 秒的发现期限（`discoveryTimeoutMs` 默认值），超时就在本会话里丢弃它（机器忙到连 Qwen 自己的工作都排不上时会发生），并把 MCP 工具藏在 `tool_search` / `tool_call` 之后，所以一个会话的首个回合只用来开会话；opencode 的 `_meta` 形态没有核对，它的请求照旧交给 broker。
- **Host 会话级 MCP 绑定**：ACP Server 侧收到 host 传入的 `mcp_servers` 时，经 `mcp_converter.convert_acp_mcp_servers` 转成 `MCPConfig`，随新建的会话绑定进 agent 管线并记 `acp_host_mcp_bound` INFO（`method`、`raw_count`、`bound_count`）。`new_session` / `fork_session` 总会新建会话并绑定；`resume_session` 只在会话不存在时新建并绑定；`load_session` 只在会话已存在时返回，传入的 `mcp_servers` 被转换并记日志，不改变已有会话的 MCP 绑定
- **失败分类**：`runtime/cli/failure.classify_failure` 按 CLI 自己的话归类——Claude `result` 行的 `error_max_turns` 不是失败（见「Max Turns 双层安全护栏」，回合以 `DONE` 收尾）；`result` 带 `api_error_status` 时只有 401 → `AUTH_FAILED`（403 是地区封锁或组织策略，Claude 把它和没登录归在同一个 `authentication_failed` 类别，但叫用户重新登录会把人指向错误的方向，所以归 `UNKNOWN`），没有状态码时 `assistant` 行的 `error: authentication_failed`（根本没发出 API 请求，即没登录）→ `AUTH_FAILED`；429 → 可重试的 `RATE_LIMITED`、5xx → 可重试的 `UNKNOWN`；没有上述判决时非零退出码才是 `PROCESS_CRASHED`（带最后一条顶层 `error` 通知，没有则取 stderr 摘录）；本运行时自己停掉的进程一律是 `CANCELLED`（唯一的例外是下一条的 401 快速失败）。拉起进程时程序不存在（`CliLaunchError`）→ 单个 `BACKEND_NOT_FOUND` 错误事件，不做续接失效重试。顶层 `error` 行不是失败：CLI 可能每次请求重试都打印一条，它们只作为通知被记住，判决只来自 `result` / 退出码。消息是 CLI 的原话，经 `event_text.condense_failure_text` 整理成一行有界文本——引用的整页 HTML（代理、防火墙、地区封锁页）缩成 `[web page: <标题>]`，空白折叠，超过 `FAILURE_TEXT_CHARS`（500）字符截断，普通单行消息原样通过；不夹带产品设置建议（给用户的处置指引由宿主按 `AcpErrorCode` 另行组织）
- **Codex 的失败分类**：`runtime/codex/failure.classify_turn_error` 读 app server 附在失败回合上的结构化 `codexErrorInfo`（一个类别，或以类别为唯一键、带 `httpStatusCode` 的对象），不看措辞猜：类别 `unauthorized` 或状态码 401 → `AUTH_FAILED`；403 不是登录问题（地区封锁也这样回答），归 `UNKNOWN`；`rateLimitExceeded` 或 429 → 可重试的 `RATE_LIMITED`；`serverOverloaded`、`internalServerError` 或 5xx → 可重试的 `UNKNOWN`；其余 → 不可重试的 `UNKNOWN`；消息是 Codex 的原话，同样经 `condense_failure_text` 整理。server 进程在回合中途退出 → 可重试的 `PROCESS_CRASHED`，带退出码与 stderr 摘录（下一回合重新拉起 server）；程序不存在 → `BACKEND_NOT_FOUND`。`test_a_failing_model_api_is_reported_as_a_retryable_failure` 与 `test_a_failure_that_is_not_a_refused_login_does_not_send_the_user_to_sign_in` 用真实二进制与只返回错误的假模型 API 守这些分类
- **401 快速失败**：登录被拒时 CLI 会连续重试数分钟才报错。实测（真实二进制、没有登录、本机假 401 服务）：Claude Code 2.1.199 与 2.1.310 都发出 11 次请求（首次加 10 次重试），分别在 183.5 s 与 184.9 s 后才以 `result`（401）结束。`TurnParser` 数连续的 401 重试信号——Claude 的 `system/api_retry`（`error_status == 401`）——满 3 次（`runtime/_base.UNAUTHORIZED_RETRIES_LIMIT`）就置位 `auth_rejection`（CLI 自己的原话）——第 3 个信号在 Claude 里出现在 2.7 s，修复后回合在 3.6 s 内结束；`CliRuntime` 记一条 `cli_auth_rejected` WARNING、终止进程，回合以 `AUTH_FAILED` 结束，这个判决优先于「自己停掉的进程是取消」。重试之间出现任何正常输出，或一次非 401 的重试，计数清零；一次 401 重试（CLI 自己刷新令牌）不会触发。Codex app server 同理：`TurnTranslator` 数连续的 401 重试通知，满同一个上限就 `turn/interrupt`，回合以 `AUTH_FAILED` 结束并记 `codex_auth_rejected` WARNING；`test_a_refused_login_fails_the_turn_within_seconds_instead_of_retrying_for_minutes` 用真实二进制与只回 401 的假模型 API 断言回合在 60 s 内以 `AUTH_FAILED` 结束、API 至多收到 4 个请求。令牌自动刷新场景下是否误判，没有用真实令牌验证
- **图片传递**：宿主把图片落成本地文件，经 `run_turn(images=[路径…])` 交给能收图的后端：Claude 由 `claude.with_image_blocks` 写成输入里的 base64 块（`CliRuntime` 先在线程里用 `runtime/image_blocks.load_image_blocks` 读文件，再交给它；其他 CLI 不收图），Codex 与 ACP 各按下面的方式。**Claude**：用 stream-json 输入。只有带图的回合才加 `--input-format stream-json`，stdin 写一行用户消息 `{"type":"user","message":{"role":"user","content":[<image 块…>,<text 块>]}}`——图片在前并保持给定顺序，提示词在后（提示词为空白时不写 text 块）；不带图的回合仍是纯文本 stdin。图片字节就在请求里，所以不靠模型去调用 `Read`、与文件位置无关（工作目录之外也行，不需要 `--add-dir`）、与权限模式无关。不用 `@"<路径>"` 文件引用：实测（真实二进制 2.1.199，本机桩 API 记录请求，没有登录、不调用模型）该引用只附 ≤ 256 KiB（262144 字节）的文件，更大的文件不报错、不告诉模型、直接不附，而截图通常比这大。实测 image 块：1×1、200 KiB、300 KiB、4.7 MiB（小于服务端每张 5 MiB 的暂存上限）都进了第一次请求，`default`（`safe`）与 `bypassPermissions`（`allow_all`）两种权限模式下都成立（300 KiB）；两张图相邻进入同一条消息；续接回合（`--input-format` 在 `--resume` 之前）的图片排在历史之后进入请求。CLI 会把大图缩小再发（4.7 MiB 的 PNG 到请求里是约 0.66 MB 的 base64 JPEG）。事件流仍是 system / assistant / result，`TurnParser` 不变。格式按文件头判定（PNG / JPEG / GIF / WebP，与扩展名无关）；读不了或不是图片的文件被略去并记 `image_skipped` WARNING（不含路径），全部被略去时该回合退回纯文本。`tests/toolkits/acp/runtime/cli/test_real_cli_images.py` 用真实二进制守这些行为（每周的 CLI 契约任务运行）。单个回合 8 张 4.7 MiB 的 PNG（共约 37.5 MiB，stdin 约 50 MiB）全部附上、没有卡住（一次手工测量，不在自动测试里）。未验证：Windows；低于 2.1.199 的版本；真实登录下模型对图的回答；真实 API 对一个回合里多张图总大小的限制。**Codex**：app server 的 `turn/start` 输入里每张图是一个 `{"type":"localImage","path":…}` 项，`CodexRuntime` 只交文件路径，由 Codex 自己读文件、解码后发给模型，不经命令行参数；`test_images_reach_the_model_as_files_not_arguments` 用真实二进制与假模型 API 断言请求里带有 `input_image` 与 `data:image/png;base64,`，协议字段由 `test_real_codex_protocol_contract.py` 对着该二进制生成的 schema 核对。**ACP**：`AcpRuntime` 在 `initialize` 之后读 `agentCapabilities.promptCapabilities.image`，为真时把同一批图片（同一个 `load_image_blocks`，同一份文件头判定）写成 `session/prompt` 里文本块之后的 `ImageContentBlock`（`data` 为 base64、`mimeType` 为判定出的类型）；没声明的 agent 只收文本块（该回合记 `acp_runtime_images_skipped` INFO，并以一条 `images_not_sent` status_update 开头；宿主提示词里的图片路径一行仍是兜底）。实测（本机子进程假 ACP agent，走生产的 `spawn_agent_process` 路径）：声明图片提示的 agent 按顺序收到全部图片块，5 MiB 的图片（base64 后约 7 MB 的单行消息）完整到达、摘要一致；未声明的 agent 只收到文本且回合正常结束。实测 OpenCode 2.0.32 与 cursor-agent 的 `initialize` 都声明了 `image: true`。未验证：真实 agent 对图片块的回答，Windows。
- **启动参数合并**：`auth.resolve_cli_args` 按「选项单元」（标志加其后的值，或开头的子命令）合并预设的基线参数与配置里的参数：配置里复述的基线选项（预设的 `--output-format stream-json`）只出现一次，配置自己重复写的选项全部保留（`--add-dir a --add-dir b` 是两个设置，不是重复）；权限模式的参数仍由映射独占。Codex 经 `auth.resolve_app_server_args` 启动：契约的 `app-server` 子命令之后只跟配置里仍然有意义的部分——`-c/--config` 覆盖、`--enable` / `--disable`、`--strict-config`；按 `codex exec` 写的旧配置里的 `-m/--model`、`-p/--profile` 译成对应的 `-c` 覆盖，其余（`exec`、`--json`、沙箱与审批旗标……）去掉并记 WARNING `codex_launch_args_dropped`，因为审批策略与沙箱是宿主随每个回合给出的；`api_key` 模式再追加 `-c cli_auth_credentials_store="ephemeral"`
- **Codex 版本下限与续接失效**：握手时 `initialize` 的 `userAgent`（`<客户端名>/<server 版本> (…)`，客户端名就是宿主自报的 `clientInfo.name`）给出 server 版本，低于 `protocol.MINIMUM_SERVER_VERSION`（0.167.0）→ 回合以不可重试的 `PROCESS_CRASHED` 结束，消息（英文）说明已安装的版本、需要的版本与「更新 Codex 后重试」，不开线程、server 进程随即收掉；读不出版本的放行并记 `codex_server_version_unreadable` WARNING。下限由真二进制实测定出：0.163.0 – 0.166.0 的审批请求里没有工具名（只有 `mcp__echo__`，缺 `ping`），人会被问「允许这次调用吗」却看不到是哪个工具，0.166.0 上其余 37 项契约测试全绿；0.167.0 与最新的 0.172.0 都让三份真二进制测试文件（38 项）与 HTTP MCP 测试全绿，MCP 测试另在 0.163.0 – 0.172.0 的 13 个稳定版上逐版本跑过。CI 的 `codex-floor` 任务从代码里读出这个常量，装上恰好这一版再跑一遍真二进制测试。续接线程时只有 server 回答 `no rollout found`（磁盘上没有这个线程的 rollout）才算会话失效（`session_expired`，本回合改开新线程）；别的续接错误（鉴权、限流、server 自己的故障）原样作为失败报出，记录保留，下一回合再试，不把一次暂时的失败当成「对话没了」
- **真二进制契约门禁**：运行时传给 CLI 的每个启动参数与档位取值（CLI Agent 经 `resolve_cli_args` 的每种权限模式，ACP Agent 经 `resolve_acp_args` 的契约参数，app-server Agent 经 `resolve_app_server_args` 的整条 argv）、整条 argv，以及 ACP 代理对 `initialize` 的回答里运行时依赖的能力，都由 `harness-cli-contract.yml` 对最新的真实 claude / codex / gemini / qwen / opencode 核对，涉及 `toolkits/acp` 的 PR、每周、手动与每次发版前（红灯不上传 wheel）都跑；不需要登录也不调用模型。旗标与取值的判官是解析各 CLI 帮助文本的 `tests/toolkits/acp/cli_contract.py`（commander / clap / yargs 三种格式），取值再经带标定的探测确认（真值通过且垃圾值被拒才算数）；帮助里没有的隐藏旗标（claude 的 `--max-turns`、`--permission-prompt-tool`）由整条 argv 的验证兜底——以不存在的会话 id 续接，claude 回答 `No conversation found`，说明参数全部被接受；Codex 的 `app-server` 子命令与覆盖旗标在 `codex app-server --help` 的范围里核对，它说的协议另由 `test_real_codex_protocol_contract.py` 对着该二进制生成的 schema 逐项核对。握手的判官是 `tests/toolkits/acp/acp_contract.py::handshake_gaps`，按子集比较：代理多宣告能力不算回退，运行时依赖的能力消失才变红。判官本身由不需要任何二进制的单元测试钉住（取走一个旗标、改错一个取值、去掉一项能力都必须变红）；ACP 的 Gemini / Qwen 另由 `tests/toolkits/acp/runtime/acp/test_real_acp_session_mode.py` 守：真实二进制 `session/new` 报告的模式是 `default`（设置文件把模式写成不问也一样，对照组证明设置确实起作用），没有登录时错误码是 -32000，旧预设存下的参数仍能启动；权限往返由 `tests/toolkits/acp/runtime/acp/test_real_acp_permission_round_trip.py` 守（脚本化的假模型 API 驱动真实二进制，见上文「Gemini / Qwen 权限往返的真二进制事实」）；宿主的 HTTP MCP Server 由 `tests/toolkits/acp/test_real_http_mcp.py` 守（claude / codex / gemini / qwen 各自带 Bearer 连上本机端点并调用工具，只读的不问、否则问；Gemini 在未受信任的工作区不连）
- **静默心跳**：CLI 回合里 stdout 静默满 15 s（`runtime._base.KEEPALIVE_SECONDS`），`CliRuntime` 每 15 s 发一个 `STATUS_UPDATE(status="working", message="<已运行时长>")`（`_base.working_status` 构造，不用 `warning`：工具路径把 `warning` 当错误计数）；`AcpRuntime` 的事件队列静默满同样的时长，也发同一个状态；有输出的回合不发。宿主若有“静默多久算卡死”的看门狗（Server Gateway 的 `execution_timeout`，默认 300 s），这个状态让一次长时间无输出的编码回合不被它误杀；整个回合仍受 `timeout_seconds` 约束
- **ACP SDK 缺失**：`acp` 是可选 extra（`myrm-agent-harness[acp]`）。`AcpRuntime` 在拉起进程之前先确认 SDK 已安装，缺失时回合以单个 `BACKEND_NOT_FOUND` 错误结束，消息带安装方式，并记一条 `acp_sdk_missing` WARNING；调用方拿到的是这个错误，而不是 `ModuleNotFoundError` 的调用栈
- **跨平台进程组清理**：每个 CLI 回合（Codex 的 app server 进程同理）都在独立进程组里运行（POSIX `start_new_session`，Windows `CREATE_NEW_PROCESS_GROUP`），停止时整组终止（POSIX `killpg`，Windows `taskkill /T /F`），子进程随之清理；回合以任何方式结束（正常、异常、取消、超时、消费方提前放弃生成器）都经同一个 `runtime/process.spawn_managed_process` 上下文停掉仍在跑的进程
- **委派取消传播**：通过 ContextVar 传递 `CancellationToken`；用户停止（或请求被取消）时，`invoke_acp_agent_tool` 借 `cancel_hook.on_token_cancelled` 在令牌被取消的一个轮询周期（0.25 s）内调用 `pool.cancel()` → `backend.cancel()`，外部 Agent 此刻没有任何输出也会停下，包括 CLI 还没开始读 stdin 的时候：提示词由 `ManagedProcess.start_prompt` 在回合旁边的任务里写入（先在调用方同步编码，编不出的提示词当场失败），不在回合之前等它写完，所以几 MB 的提示词或图片卡在管道里时，回合照常流式输出、停止立即生效，`close` 结束这个写入任务（CLI 的子进程若自开会话并继承了 stdin，管道不会断，任务靠取消结束）。实测（真实 claude 2.1.199，本机桩 API，`SessionStart` 钩子 `sleep 12`，回合带 4.7 MiB 的图，1 s 时停止，各测 3 次）：按停止后回合立即结束（均小于 0.1 s，整个回合 1.0 s）；若回合要等提示词写完才开始，同一场景实测按停止后 11.4～11.5 s 才结束（整个回合 12.4～12.5 s，即等钩子跑完）。`test_real_cli_images.py` 里对应的自动测试（`-m integration`，停止后 5 s 内结束）在 2.1.199 与 2.1.177 上都通过，回合等待写入完成的实现过不了它。`AcpRuntime` 的停止同样不依赖 agent 还在读（`runtime/acp/stalled_agent.py`）：`session/cancel` 排在一个 agent 不再读取的大提示词之后时，`cancel` 在 5 s 后结束 agent 及它启动的全部进程（`acp_cancel_timeout` WARNING，`end_agent`）；`close` 的传输退出也以 5 s 为界，超时同样结束整棵进程树（`acp_transport_exit_timeout` WARNING）——SDK 的发送循环会吞掉对它的取消，只有 agent 与它的工具进程都结束、管道读端释放，卡住的写入才会失败。SDK 把 agent 起在宿主自己的进程组里，不能按组发信号，所以按进程树逐个结束（`code_execution/session/process_tree.py` 的 `kill_process_tree`：先快照后代并 SIGTERM，0.5 s 宽限后 SIGKILL）；Windows 走 `taskkill /F /T`（立即强制终止，没有宽限期），未验证。实测（子进程假 agent 在 `session/new` 之后睡 60 s 不读 stdin，回合写 5 MiB 图片）：没有界限时 `cancel` 会一直排在卡住的写后面，直到 agent 醒来（测试以 20 s 超时判红）；`cancel` 与 `close` 在界限（测试里调成 1 s）加不超过 0.5 s 宽限内返回且进程已死；健康 agent 的 `cancel` 不会杀进程。假 agent 在 `session/new` 时再起一个继承它 stdin 与 stdout 的工具进程（`sleep 300`，或监听端口的 `http.server`），界限 5 s，各测 3 次：`cancel` 加 `close` 与只 `close` 都是 5.5～5.7 s，工具进程已死，端口已释放；无工具进程的基线是 5.0～5.1 s。对照：只结束 agent 进程、不结束它的工具进程时，工具进程仍活着、端口仍可连接，而且 `close` 要 10.5 s、`cancel` 加 `close` 要 15.5 s——工具进程握着 agent 的 stdin 读端，agent 死后卡住的写入也不会失败，SDK 退出一直不返回（agent 已 `rc=-9`，退出 10 s 后仍未完成），这个数字超过服务端给整池关闭的 10 s 上限（`_POOL_CLOSE_TIMEOUT_SECONDS`）；工具进程不继承 stdin 时对照为 5.5 s。真实 agent 的工具进程是否继承 stdin 因 agent 而异，没有逐个核对。agent 被结束后，挂着的 `session/prompt` 以 BrokenPipe 失败；服务端委派流与 `invoke_acp_agent_tool` 的循环都在令牌取消后的下一个事件处 `pool.cancel` 并关闭回合，此时 `_do_run_turn` 只记 `acp_prompt_task_failed_after_close`（INFO），不用这个失败替换关闭（否则 `run_turn` 会在被关闭的生成器里再 yield 一个 ERROR 事件，抛 `RuntimeError: async generator ignored GeneratorExit`，委派流收不到 CANCELLED）。实测（`stream_direct_delegate` + `RuntimePool` + 卡死且带工具进程的假 agent，界限 5 s，3 次）：令牌取消到流结束 5.15、5.24、5.25 s，流以 `cancelled` 结束，工具进程已死，随后 `pool.close_all` 0.00～0.02 s。SDK 自己在每次强制停止时仍会在 root logger 记一条 `Send loop failed` 的 ERROR，不在我们的控制内。`CliRuntime` 对整个进程组 SIGTERM（5 s 宽限后 SIGKILL），清理在调用方再次被取消时也会跑完（shield）；停止落在进程尚未创建的瞬间（正在加载会话）时，会在进程创建后立即生效；被本运行时停掉的进程按 `CANCELLED` 处理而不是崩溃（真实 claude 被 SIGTERM 后以 143 退出，不能当作续接失败或崩溃）。宿主进程自身被强杀不在保证范围内
- **直连路由模式**：`force_external_agent` 参数允许前端绕过 LangChain Agent，直接将请求路由到指定外部 Agent，零 LLM 开销、零延迟的流式响应；**Server `forced_delegate` 在 RuntimePool 之前评估 `invoke_external_agent`**——判的是与模型发起的 `invoke_acp_agent_tool` 同一份有效策略（通道预设、已保存配置、Agent 自己的覆盖、请求覆盖），判之前先像普通回合那样重载它的组成；仅 `DENY` 立即 SSE error（不可绕过 Permission Engine），`ASK` 视作用户已确认（用户在输入框点名了这个 Agent，没有第二次审批可问），`ALLOW` 放行；拒因分远程连接、会话预设（只读）、策略三类，各给不同的处置指引
- **流式文本推送**：Server 的 `services/external_agents/delegation/stream.stream_direct_delegate`（由 `ExternalAgentsMixin._direct_delegate_stream` 调用）把 `pool.run_turn` 的 RuntimeEvent 实时转换为前端 SSE 事件：TEXT_DELTA → MESSAGE、REASONING_DELTA → REASONING、TOOL_START / TOOL_RESULT → TASKS_STEPS、USAGE_UPDATE → TOKEN_USAGE、其余见下。连接前发送 connecting 状态，结束时发送 completed / stopped / failed 状态步骤与 MESSAGE_END，提供全生命周期进度反馈；翻译层只依赖 harness 的 `DelegationPool` 协议
- **REASONING_DELTA 前端传递**：`invoke_acp_agent_tool` 工具将外部 Agent 的思维链（REASONING_DELTA）通过 `ToolProgressSink` 实时推送至前端，用户可看到外部 Agent 的推理过程
- **CLI Session 复用**：CLI 回合最先输出的一行 announce 会话（Claude `system/init` 的 `session_id`），`CliRuntime` 在收到 announce 的那一刻就记录（连同创建它的工作目录，`CliSessionRecord`）并写穿到宿主 `CliSessionStore`，所以回合中途被停止的会话照样能续接；记录只看 announce，不看回合结果。续接由 `claude.with_resume` 完成：Claude 追加 `--resume <id>`；id 必须是规范 UUID 形态才会被重放。Claude Code 按工作目录存放会话（v2.1.223 之前 `--resume <id>` 只在当前项目目录里查找 id），所以记录目录与本回合启动目录不同就不续接，改开新会话并发出 `session_workspace_changed`。**失效自愈**：续接的判据是 CLI 在 announce 之前、没有任何输出、以正退出码退出（它自己在说「不认识这个会话」），此时发出 `session_expired`，同一回合内改开新会话重跑一次；旧 id 在新会话 announce 之后才被替换，重试也失败则旧记录原样保留。被本运行时取消或超时杀掉的进程说明不了会话是否存在，记录保留。Claude 之外的 CLI 按配置原样启动、不续接。**Codex 的会话是 app server 的线程**：`runtime/codex/threads.ThreadBook` 记录 harness 会话对应的线程 id（连同创建它的工作目录，写穿到同一个 `CliSessionStore`）；同一个 server 进程里直接在已加载的线程上继续，新进程（或宿主重启）经 `thread/resume` 从 Codex 留在磁盘上的 rollout 续接，server 不认识这个线程（被删除或从未保留）时开新线程并发出 `session_expired`，记录的工作目录与本回合不同时开新线程并发出 `session_workspace_changed`。没有 `CliSessionStore` 时线程是临时的（`ephemeral`），不在用户自己的 Codex 历史里留下任何东西，`release_resident` 释放 server 后下一回合重新拉起它但不再认得上一个对话；有 store 时同一个对话继续（`test_real_codex_threads.py` 的 `test_a_conversation_continues_in_a_new_process_from_the_store`、`test_a_thread_the_server_does_not_know_starts_a_fresh_one_and_says_so`、`test_a_released_pool_starts_the_server_again_for_the_next_turn` 用真实二进制守这些行为）
- **Per-backend 并发串行化**：`RuntimePool.run_turn` 在全局 Semaphore 之外对同一 backend 增 per-backend `asyncio.Lock`，保证单后端实例（如 CliRuntime 的可变进程句柄）同时只跑一个 turn；`cancel()` 不取该锁（调用方在 turn 循环内取消，无死锁），不同 backend 仍可并行
- **超时/异常/放弃的进程清理**：`BaseRuntime.run_turn` 用 `aclosing` 包住回合生成器，超时、意外异常或消费方提前放弃都会让回合收尾并停掉外部进程；超时与异常再先 `cancel()` 后产出 ERROR 事件，杜绝孤儿进程在后台继续消耗资源
- **失败路径统一 ERROR**：一个失败的回合只以一个 ERROR 事件结束，不会被静默降级为 DONE 导致 delegate 误判成功：`AcpRuntime._do_run_turn` 在事件被消费到底之后的 prompt 失败会 re-raise，由 `run_turn` 转为 ERROR 并清理进程（消费方先关闭了回合则不同，见停止一节）；`CliRuntime` 在回合末用 `classify_failure` 给出分类后的 ERROR（CLI 已流式给出 ERROR 事件时不重复；CLI 已产出文本、没有 `result` 行却以非零码退出时记 warning 并按 DONE 结束；`result` 行报错时一律是 ERROR）
- **NDJSON 解析分层**：`turn_parser.TurnParser`（有状态、无 I/O）+ `parser.py`（各格式共享的事件构造），任何 CLI 输出形态都能只凭文本做单测
- **增量文本与工具事件**：Claude 以 `--include-partial-messages` 启动（仅 stream-json 输出），文本逐段到达，随后重复的完整消息按 API message id 去重；assistant 消息里的 `tool_use` 与 user 消息里的 `tool_result` 映射为 `TOOL_START`（含一行摘要）/ `TOOL_RESULT`
- **大输出行**：stdout 单行上限 16 MiB（一条工具结果可以是整个文件，asyncio 默认的 64 KiB 会让整回合失败）；stderr 只保留末尾 8 KiB 用于错误摘录
- **Delegate 元数据 TypedDict**：`DelegateUsage` / `DelegateMeta` 提供精确的类型提示
- **直连模式 Session 隔离**：基于 `chat_id` 生成独立 `session_id`，不同对话的外部 Agent 上下文互不干扰；用户直接指名的回合与模型调用工具的回合经 `types.delegation_session_id(agent, scope)` 取同一个 session id，对话从任一入口开始都能被另一入口续接
- **直连模式工具步骤**：外部 Agent 的每次工具调用是一个带 `tool_call_id` 的独立步骤（`delegation_<agent>_tool`，步骤文本为一行「agent: 工具 — 摘要」），对应的 TOOL_RESULT 更新同一步骤的状态（失败时带 CLI 原文的前 300 个字符），而不是覆盖上一个步骤；前端以这一行作标题，不再重复渲染
- **直连模式收尾与错误处理**：直连路径不重跑整个回合；`CANCELLED`（含用户停止、令牌被取消）只发出 CANCELLED 事件、不发完成状态，`DONE` 的 `stop_reason=max_turn_requests`（外部 Agent 到了自己的轮次上限）按「已停止」收尾而不是错误横幅，其余 ERROR 转成前端 error 事件并以 failed 状态步骤收尾。唯一的自动重来是 `CliRuntime` 对被 CLI 拒绝的续接在同一回合内改开新会话（见「CLI Session 复用」，用户看到一条只带标题的提示）；`invoke_acp_agent_tool` 工具路径另有对 `retryable` 错误的一次重试

---

## 7. 不做的事情（避免过度设计）

| 不做 | 原因 |
|------|------|
| 容器化执行 | 不是 Sandbox，不需要 Docker 沙箱 |
| 15+ 后端 | ACP 与 CLI 两种后端已覆盖主流场景 |
| Transcript Hash Resume | ACP 协议已有 session resume |
| 流式消息缓冲 | 场景不需要 120ms 批量写入 |
| Config Watcher | 配置变更通过重启处理 |
| Cron 集成 | 不在 ACP 模块职责范围 |
| NCP 自研协议 | ACP 已是行业标准 |
| 三阶段执行 | plan/execute 在 Agent 层处理 |

---

## 8. 设计特性与已知局限

**设计特性**：

1. **Runtime 与 Server 解耦**：外部 Agent 集成不污染 IDE 桥接路径
2. **抽象层次稳定**：`RuntimeBackend` 统一 ACP 与 CLI 两种接入方式，扩展成本低
3. **生命周期完整**：run_turn + cancel + resume + close + get_info
4. **结构化错误**：错误码 + retryable + 详情字段
5. **事件模型克制**：8 种事件类型覆盖委托与观测需求
6. **安全默认**：环境清理、白名单、工作目录内 FS 回调
7. **边界清晰**：明确非目标列表，避免范围蔓延
8. **Server 层职责单一**：桥接与翻译与 Runtime 正交
9. **依赖面小**：标准库 + 可选依赖 `agent-client-protocol` + 可选 Node 工具链
10. **并发受控**：RuntimePool Semaphore
11. **框架/业务可替换**：RuntimeBackend、CliSessionStore 等 Protocol 注入点明确
12. **全链路取消**：CancellationToken 通过 ContextVar 传播，用户取消 → `cancel_hook` 在一个轮询周期内触发 → pool.cancel() → backend.cancel() → 进程组终止（SIGTERM → 宽限 → SIGKILL），不依赖外部 Agent 是否还在输出（含提示词尚未被 CLI 读走的时候，见「委派取消传播」）
13. **超时/异常/放弃不留孤儿**：回合生成器由 aclosing 收尾，超时、异常与消费方提前放弃都会停掉存活进程
14. **双层 Max Turns 安全护栏**：Layer 1（CLI 原生上限）让支持的 CLI 自行停止并如实报告，Layer 2（delegate_tool 工具调用计数）只兜底没有原生上限的后端
15. **Agent 能力描述**：description 注入 tool description，帮助 LLM 在多 Agent 场景下做出正确的委派决策
16. **MCP 注入能力守卫**：`supports_mcp` 在 `RuntimePool` 层统一校验，避免向不支持的 ACP 后端注入 MCP 导致 session 崩溃；直接调用 `CliRuntime` 与 ACP Server 侧 host MCP 均显式告警，防御路径零静默降级；没有交出的 Server 一律以 `mcp_not_attached` 状态更新告知宿主

**已知局限**：
- 能不能逐条询问取决于 Agent 自己：Claude Code（`--permission-prompt-tool`）与 ACP Agent（`session/request_permission`，含 Gemini、Qwen）能问；Codex 经 app server 能问，但它只在动作超出只读沙箱时才发审批请求，是否去申请由模型决定（真实模型的表现没有验证）
- ACP 的拒绝没有携带理由的位置，人写的拒绝理由只有 Claude Code 收得到
- 宿主进程自身被强杀时，已启动的外部 CLI 进程不在清理保证内
- 会话续接与原生轮次上限只覆盖 Claude（Codex 的续接走线程）；其他 CLI 按配置原样启动
