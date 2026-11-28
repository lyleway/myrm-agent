# api/external_agents/

## 架构概述

外部 Agent 连接档案 HTTP 层。上级文档：[../_ARCH.md](../_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | External delegated agent auth endpoints. | ✅ |
| `_deploy_capability.py` | 门控 | `require_subscription_login_capability()`：部署不允许订阅登录（托管沙箱）时返回 403；登录与终端登录两个入口共用，fail-closed；`require_backend_allowed()`：部署不运行的 Agent 的安装接口以 403 拒绝。 | ✅ |
| `_account_scope.py` | 解析 | `resolve_account(backend, account, create=…)`：请求指名的账号 → 账号对象（`None` = CLI 自己的 home）；`account_env` 在它之上给出能到达该账号目录的环境；名字或 Agent 不合法 400；账号不存在时登录与终端登录先建出它（别的机器同步来的配置指名的账号在本机还没有，账号在被用到时才存在），退出登录则 404（没有可登出的）；三个入口共用，对不可用的名字回答一致。 | ✅ |
| `accounts_router.py` | 路由 | 命名账号 HTTP API（`router.py` 把它挂在 `/accounts` 下）：`GET /accounts` 列出全部账号及登录态（经 `services/external_agents/login_state.py`：CLI 能自报登录态的以 CLI 在该账号目录里的自报为准，否则看凭据文件，各账号并发探测），`POST /accounts` 建空账号（受 `_deploy_capability` 门控；409 已存在，400 名字或 Agent 不合法），`DELETE /accounts/{backend}/{name}` 删除账号及其登录（始终可用，托管化之前遗留的账号能被看到和删掉；先让该账号的 CLI 自己登出，登录仍在则 409 并保留账号——放在钥匙串里的登录删目录够不着，删了就再没有任何东西能移除它；删目录在线程里做）。 | ✅ |
| `router.py` | 路由 | 外部 Agent 订阅鉴权 HTTP API。`/auth/status` 强制 fresh detect（`refresh=True`）返回各 backend 安装/登录态、该 Agent 的契约通道（`transport`：`cli` 或 `acp`，设置页据此只读显示类型）与该部署是否允许订阅登录（`subscriptionLoginAllowed`，Settings badge 与表单据此取舍登录入口），并回填进程级探测缓存鲜度；不含 ephemeral RuntimePool health metrics。宿主从不经手凭据：没有导入凭据、回填验证码的接口，登录由 CLI 自己完成，宿主只读出「谁登录了」。登录与终端登录受 `_deploy_capability` 门控；退出登录始终可用，使托管化之前留下的凭据能被移除，它经 `services/external_agents/logout.py` 让 CLI 自己登出再清文件，返回的是 CLI 事后自报的登录态（登录仍在就照实报告）。状态行的 `loginStrategy` 说登录由谁跑：`browser_oauth`（宿主驱动 CLI 的登录并转交链接/设备码，目前只有 Codex）或 `terminal`（用户在自己的终端里跑 CLI 自己的登录，Claude、Gemini、Qwen）。`/auth/login`（SSE）只服务前者，对后者 400；请求可带 `remote`：用户的浏览器与本服务不在同一台机器时为真，CLI 据此走在任何浏览器上都能完成的登录（Codex 走设备码），关闭这条 SSE 就停掉 CLI，没有别的会话登记或取消接口。`POST /auth/terminal-login`（请求 `backend` 与可选 `account`）给出该账号专属的官方登录命令（每种 shell 一条：POSIX、PowerShell、cmd，cmd 拼不出的写法该 shell 缺席；CLI 是工具链装的就带上它的 Node；账号 home 变量在命令里）、`defaultShell`，以及 CLI 自己对该 home 的登录态；用户跑完后再问一次就是「重新检测」。CLI 没装 409（不会为此建账号），Agent 不认识 400。登录、终端登录、退出登录的请求体可带 `account`：作用于该账号目录里的登录，而不是 CLI 自己 home 里的；账号还不存在时登录与终端登录先建出它，退出登录 404。 | ✅ |
