# services/external_agents/

## 架构概述

外部 CLI 委托运行时生命周期（Server 业务层）。与 `api/external_agents/`（订阅鉴权 HTTP）和 `ai_agents/general_agent/external_agents.py`（装配 Mixin）分工：`RuntimePool` 按 **chat** 复用（`--resume`），`track_turn` 记录同 chat 的在途 `run_turn`（同一后端的 turn 由 `RuntimePool` 串行，同 chat 里不同 agent 的 turn 并行）；CLI 会话 id 落在 chat 行（`ChatCliSessionStore`），服务重启后同一 chat 仍续接同一个外部对话。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | 包导出 Registry / Facade | — |
| `accounts.py` | 核心 | 命名账号的位置与 Agent 跑在哪个 home：`account_store()`（状态根 `resolve_workspace_state_dir()` 下的 `external-agents/accounts`，每次现建、根跟随 `MYRM_DATA_DIR` 且总是绝对路径）与 `agent_home(command, account)`（托管部署 → 稳定的 api_key 目录；否则该 Agent 指名的账号目录，不存在就创建；没有指名 → `None` = CLI 自己的 home；自定义命令不是已知 Agent，无法被重定向，忽略 `account` 并记 WARNING）。harness 只管怎么把 CLI 搬进一个 home，这里决定搬进哪个 | ✅ |
| `deployment_policy.py` | 核心 | `blocked_backend(command_or_backend)`：一条命令（或后端名）指的是这个部署不运行的哪一个已知 Agent，没有就 `None`。规则表在 `platform_utils/deployment_capabilities.py`（`blocked_external_backends`），这里只做「命令 → 已知 Agent」的匹配，用 harness 的 `profile_for` 同一条规则，所以 `claude-code`、`codex-cli` 这类带着名字的命令同样被挡；安装接口、运行时装配、状态接口都问这一个函数 | ✅ |
| `memory_grants.py` | 核心 | 外部 Agent 的只读记忆凭据：`MemoryGrants.issue(owner, agent_id=…, external_agent=…)` 为一个池（`owner`）里的一个外部 Agent 签发令牌（`myrm_dlg_…`，只存摘要）；`/mcp` 中间件用 `resolve` 认出它（同时记下 Agent 已连上），`revoke(owner)` 在池关闭时收回，池被回收也会随 `weakref.finalize` 自动失效。`memory_server_for` 给出交给 harness 的 `HttpMcpServer`（`myrm-memory`，本机 `/mcp/` 地址，`Authorization: Bearer`，`read_only`），`memory_connected(config)` 在回合结束时判断 Agent 有没有连上它（等最多 2 秒，只对交出了记忆的 Agent 才付这笔等待） | ✅ |
| `logout.py` | 核心 | `log_out_of_home(backend, env)`：让本机检测到的该 CLI 在 `env` 指向的 home 里自己登出，再清凭据文件，返回 CLI 事后自报的登录态（没装 CLI 就只清文件）。退出登录接口与删除账号共用：放在钥匙串里的登录只有 CLI 自己能移除，删文件或删目录都够不着 | ✅ |
| `cli_session_store.py` | 核心 | `ChatCliSessionStore`：harness `CliSessionStore` 协议的 chat 行实现。`load` / `save` 经 `ChatCliSessionRepository` 读写 `chats.external_cli_sessions`（`save` 在 SQLite 写锁内读后写，并发的两个后端各自写自己的 session 名互不覆盖；不触碰 `updated_at`；不存在的 chat 与无痕 chat 不落库）；损坏的条目按“无会话”读出。异常原样上抛，由 harness 降级为新会话 | ✅ |
| `permission_broker.py` | 核心 | `ChatPermissionBroker`（harness `PermissionBroker` 的聊天实现，冻结 dataclass：`agent_id` / `chat_id` / `workspace` / `ttl_seconds`）：外部 Agent 回合里每一次权限请求变成该聊天的一条 `external_agent_permission` 审批（severity：`execute` / `delete` / `move` 为 `high`，其余 `warning`；`reason` 是「Agent 名: 标题」；`expires_at` = 现在 + 600 s），回合在 `PendingPermissionAnswers` 的 future 上等人决定。**所有展示给人的内容先脱敏再截断**（`permission_payload`：在 20 000 字符窗口上脱敏、不可见字符转成可见转义，再截到 2 000 字符；路径最多 20 条、每条 500 字符；`command` 提到载荷顶层供批量风控与卡片读取）。四种结局：批准 / 拒绝（拒绝带人写的理由）→ 注册表 `resolve_approval` 经 `settle` 唤醒；无人应答 → 600 s 后以 `NO_ANSWER_MESSAGE` 拒绝并 `withdraw`（`TIMEOUT`）；回合先结束（停止、整体超时）→ 取消 `decide` 时 `withdraw`（`CANCELLED`，shield 内完成，卡片在所有设备上关闭）；创建审批本身失败 → 异常上抛，harness 按拒绝处理（fail-closed）。登记等待前的一次读回处理「公告期间已被决定」的竞态。回合自己的超时在等人期间由 harness 暂停，等多久是这里的规则 | ✅ |
| `delegation/` | 核心 | 直连委托（`force_external_agent`）的业务层子包：用户回合 → 外部 CLI 的提示词、CLI 的回合 → 前端 SSE 事件、失败 → 用户能处置的指引。外部只从子包门面导入。见 [delegation/_ARCH.md](delegation/_ARCH.md) | ✅ |
| `runtime_pool_registry.py` | 核心 | `ChatRuntimePoolRegistry` + `ChatScopedRuntimePoolFacade`（只暴露 harness `DelegationPool` 协议的 4 个成员，不做 `__getattr__` 透传：`prompt` / `get` 一旦透传就会让 registry 看不到在途 turn；`run_turn` 以 `aclosing` 包住池的回合，把回合的 `permissions`（权限裁决者）原样交给池，关闭 facade 的生成器会先关掉池的回合再结束在途计数）+ `close_external_agent_pool_for_chat`：acquire/release、per-chat 在途 turn 计数（`track_turn`；不做串行，串行由池按后端负责，cancel 不计入）、fingerprint 变更 defer、idle 回收（只释放常驻 ACP 进程，池与 CLI `--resume` 会话 id 保留；有在途委托的 chat 不回收，`track_turn` 退出重置 idle 计时）、`ensure_tracked`（facade 每个委托 turn 先确认自己的池仍登记在册，被 close 摘出则重新登记）、删 chat 即时 close、服务关闭 `close_all`；所有 pool 关闭 / 释放带上限（带上限、不抛，`close_all` / idle 回收并发执行）；关闭池之前先收回它的记忆凭据 | ✅ |

## 账号与 api_key 通道

- **账号 = 目录**：同一个 CLI 的多个订阅登录（工作号、个人号）各占一个独立目录 `<状态根>/external-agents/accounts/<后端>/<账号名>`，目录树本身就是登记表，没有第二份要保持同步的记录；状态根取 `resolve_workspace_state_dir()`（云沙箱跟随工作区卷）。CLI 被重定向进该目录（`RuntimeConfig.account_home`，harness 按各 CLI 的契约设置对应变量），登录、设置与历史都只落在账号里；用户自己 home 里的登录既不被碰也不会被读到。
- **Agent 配置的 `account`**：`external_agents` 配置条目里的可选字段，空表示 CLI 自己的 home（用户在本机已有的登录）。`external_agents_runtime_config` 归一化时校验名字（不合法的名字让该 Agent 被丢弃并记 WARNING，名字不会走到路径上）、把它纳入池指纹（换账号即换池）、注册时经 `agent_home` 取家目录；家目录建不出来（磁盘权限、同名文件挡路）只丢该 Agent，其余 Agent 照常注册。
- **托管通道**（`allows_subscription_login` 为假）：每个已知 Agent 跑在稳定的 api_key 目录 `<根>/_api_key/<后端>` 里。该目录从不被登录、不是账号、不会被列出，磁盘上遗留的订阅登录因此不会被一次按 key 计费的运行读到；该部署下 `account` 配置被忽略。本机的 api_key 模式不隔离（那是用户自己的机器与自己的选择）。托管部署里 claude 与 codex 被下节的后端闸门挡下，这条通道目前只服务于其余已知 Agent。

## 托管部署的后端闸门

- **一张规则表**：`DeploymentCapabilities.blocked_external_backends` 列出这个部署既不安装也不运行的已知 Agent。托管（`SANDBOX`）部署是 `claude`、`codex`；本机、桌面端与 WebUI 远程（都跑在用户自己的机器上）为空。表与「这个部署留不留订阅登录」同在 `deployment_capabilities.py`，一个部署的取舍在一处读到。
- **各处只问一个函数**：`deployment_policy.blocked_backend`。`/external-agents/install/{backend}` 在下载任何东西之前以 403 拒绝；运行时装配（`external_agents_runtime_config`）不注册这样的 Agent，也不把它计入池指纹，每建一个池记一次 WARNING（不记配置里的环境变量）；`/external-agents/auth/status` 的该行 `availableInDeployment` 为假，不启动该 CLI 去探测登录，只从文件读出遗留的登录，所以用户仍能看到并退出它。
- **前端只照状态行办**：`availableInDeployment` 为假的 Agent 在设置里不能启用、不提供预设、不能保存，只显示「此部署不可用」与（有遗留登录时的）退出；对话配置面板里外部 CLI 的就绪度为 `blocked`。
- **已知的边界**：匹配只看命令本身（可执行名，或带着 Agent 名字的名称），不扫描参数；改了名字的包装二进制无法被识别。规则表目前只含 claude 与 codex，其余已知 Agent 的托管条款尚未逐家核对，没有列入不等于已获准许。
- **换账号**：账号隔离的也是 CLI 的会话历史，同一聊天里把 Agent 换到另一个账号后，先前记下的 CLI 会话 id 在新账号里不存在，走既有的“会话失效”重试路径（改开新会话并给出一条提示），不需要另存会话与账号的绑定。

## 记忆随行（只读凭据）

- **同意在用户手里**：外部 Agent 配置的 `shareMemory` 只有显式的 `true` 才算同意；缺省、`false`、字符串 `"true"` 等一律不算，自动探测出的 Agent 从不带这一项。给了同意的 Agent 才会拿到记忆服务器，其余 Agent 与以前完全一样。
- **谁的记忆**：池所属的 Myrm Agent（`agent_id`，缺省 `default`）。池指纹把这位 Agent 纳入（仅对分享记忆的 Agent），所以换 Agent 或换同意都会换池，旧池关闭时凭据一并收回。
- **凭据的寿命 = 池的寿命**：每个池里的每个外部 Agent 一枚令牌，`close_runtime_pool_bounded` 先收回凭据再关池（即使关池卡住，已收回的凭据也不再能读记忆）；idle 回收只释放常驻进程，不收回凭据——下一回合重新拉起的进程还用同一枚。
- **只读范围**：`/mcp` 上的委托凭据只能列出、调用 `memory_recall`、`memory_list` 与（档案启用 wiki 时）`wiki_query_tool`（见 `api/mcp/delegation_scope.py`）。
- **如实告知**：harness 没有把记忆服务器交给 Agent（该 Agent 不支持接入）→ 回合内一条 `mcp_not_attached` 通知；交出了但回合结束时 Agent 从未连上 → `mcp_not_connected` 通知（失败与被停止的回合不加这条）。

## 删 chat 接线

`services/chat/chat_disposal.py` 在 soft-delete / permanent-delete / empty-trash 时调用 `close_external_agent_pool_for_chat(chat_id)`，立即释放 CLI 子进程，不等 idle 600s。

## 回收与关闭

- **idle 回收（释放常驻进程）**：仅在 `acquire` 时机性触发（无后台 reaper），对空闲超过窗口的池调用 `RuntimePool.release_resident()`：只关闭常驻进程型（ACP、Codex app server）后端，池条目与各后端记着的会话 id 保留。Claude 的 CLI 回合之间没有进程，整池关闭不释放任何资源（会话 id 另存在 chat 行里，丢的只是内存副本），所以同一个 chat 隔 >10 分钟再追问委托，仍续接同一个外部会话（能续接会话的后端：claude、codex；Codex 的 app server 被释放后，下一回合重新拉起并按 chat 行里的线程 id 恢复）。不参与回收的 chat 有两类：有在途委托的 chat（避免长委托期间 CLI 子进程被 SIGTERM），以及正在调用 `acquire` 的 chat 自己（它在被使用，常驻进程不能在取用前被释放）。idle 计时在 `acquire` / `release` / 每个 `track_turn` 退出时刷新，因此「超过 idle 窗口的长回合」结束后不会被立刻回收；已回收的池在再次被使用前不重复回收（`idle_released`）。回收期间条目被暂时摘出 `_entries`（持 registry `_lock`），此时到达的 turn 在 `ensure_tracked` 的慢路径等锁，回收完成后才继续，不会与 ACP 后端的关闭重叠；回收被取消时条目也会放回（`finally`），池不会失去登记。
- **关闭后重新登记**：池只在配置变更替换、`close_chat`（删 chat）、`close_all`（服务关闭）时被摘出登记并 close，而执行单元缓存（`ChatAgentExecutionCache`，1800s）里的旧单元仍可能握着该池的 facade。已关闭的 `RuntimePool` 仍可复用：下一次 `run_turn` 会懒重建后端，这些进程不在 registry 里，`close_chat` / `close_all` 看不到，成为孤儿。因此 `ChatScopedRuntimePoolFacade.run_turn` 在把自己计为在途后调用 `ensure_tracked`：已登记则无锁直通；未登记则以 `config_fingerprint` 重新登记；registry 已有另一个池时以 registry 为准。快速路径不拿 registry `_lock`，委托 turn 不会排在别的 chat 的慢关闭后面；idle 回收不处理有在途 turn 的 chat，检查与使用之间没有竞态。
- **配置变更**：fingerprint 变化且回合在途时 defer，回合结束后的下一次 `acquire` 再替换。
- **服务关闭**：`app/server/shutdown.py::safe_close_external_agent_pools` 由 lifespan `_shutdown` 调用 `close_all`（此时 Gateway 已 drain，在途回合已结束），不依赖 CLI 子进程随父进程退出。

- **关闭有上限**：`RuntimePool.close_all` 自身没有上限，而 registry 在持有 `_lock` 时关闭 pool，任何一个关闭不返回的 backend 都会冻结所有 chat 的 `acquire` / `release` / 删 chat。因此 registry 的 3 个关闭点（配置变更替换、`close_chat`、`close_all`）与 `GeneralAgent.release_pooled_session` / `close` 的 ephemeral 路径统一走 `close_runtime_pool_bounded`，idle 回收的 `release_resident()` 走同一个 `_bounded`：`asyncio.timeout(10s)`，超时或异常只记 warning（`runtime_pool_{close,release}_timeout` / `runtime_pool_{close,release}_failed`）不抛出，取消照常传播；`close_all` 与 idle 回收对多个 pool 并发执行，最坏耗时是一个上限而不是每个 pool 一个。这个 10s 是兜底：harness 层 `AcpRuntime` 在其下对 `session/close` 另有 4s 内层上限（且只向声明了 `sessionCapabilities.close` 的 agent 发送），使传输层收尾（终止子进程）先于外层上限完成。超过外层上限后失联的子进程不再被跟踪，随服务进程退出。

## 会话持久化（服务重启后续接）

- **写入时机**：`CliRuntime` 与 `CodexRuntime` 在后端自己宣布会话的那一刻就写 store（`CliSessionBook.persist`；Claude 的 `system/init`、Codex 的 `thread/start` 应答），不等回合结束，被用户停止或超时杀掉的回合也不丢会话；未变化不重写；每个 chat 一个 `ChatCliSessionStore(chat_id)`，由 `external_agents._build_pool` 在有 chat scope 时注入 `RuntimePool(cli_session_store=…)`；无 chat scope 的临时池不落库，oneshot 私有后端不接触 store。
- **读取时机**：能续接会话的后端（claude、codex）在后端内存里没有该 session 的记录时读一次 chat 行（进程重启后的首个回合，或此前没产生过记录的回合）；已有记录且 id 不变的回合既不读也不写（续接沿用原 session id）。记录里带创建目录，与本回合启动目录不同就不续接，并发出“工作目录变了，已开新会话”的提示（Claude Code 按目录存放会话，v2.1.223 之前 `--resume` 只在当前项目目录里找 id，之后跨项目查找，按目录判断对新旧版本都安全）。
- **写锁等待**：CLI 宣布会话时直接 await store，harness 不设超时。`save` 遇到他人长期占着 SQLite 写锁时最多等一个 `busy_timeout`（默认 30 s，上限 60 s，网络共享存储降级时 ×3），随后异常上抛，harness 记 warning，回合不受影响（失败的记录在下一次宣布时重试写入）；对 `save` 的取消也不能提前结束这段等待，所以 harness 不加超时包装。
- **失效**：CLI 在宣布会话之前、没有任何输出就以正退出码拒绝续接（会话已过期或被清理）时，同一回合内改开新会话重试一次，并发出“上次会话已失效，已开新会话”的提示；新会话被宣布后覆盖旧记录，两次都失败则旧记录保留，记录从不被删除。取消 / 超时杀掉的进程不算失效。Codex 的 `thread/resume` 被服务端拒绝（线程已被清理）时同样改开新线程并发出同一条提示。Claude Code 默认只保留 30 天会话，过期后走同一条失效路径。
- **配置变更替换池**不清记录（重启与不重启一致：上下文跟着 chat，直到 flush 或失效）。
- **上下文 flush**：`ChatService.focus_flush_session` 在清消息的同一事务里把 `external_cli_sessions` 置空，提交后 `chat_disposal.reset_external_agent_sessions` 关闭该 chat 的外部 Agent 池，两半缺一都会让下一回合续接已清掉的对话。
- **fork / 无痕**：fork（`ConversationForkManager`、`ContextBranchForkService`）显式构造新 chat，不继承；无痕 chat 不落库（服务重启后外部会话随之结束，与“不留痕”一致）。

## 依赖

- `myrm_agent_harness.toolkits.acp.runtime.pool` / `acp.types`（`CliSessionRecord`）— harness Integration 层，无反向依赖
- `app.database.repositories.chat_cli_session_repo` / `app.database.factory.acquire_write_lock` — `cli_session_store.py` 的持久化与写锁
- `app.platform_utils.deployment_capabilities` — `deployment_policy.py` 读取这个部署不运行的 Agent 的规则表
