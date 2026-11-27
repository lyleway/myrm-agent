# api/external_agents/

## 架构概述

外部 Agent 连接档案 HTTP 层。上级文档：[../_ARCH.md](../_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | External delegated agent auth endpoints. | ✅ |
| `_deploy_capability.py` | 门控 | `require_subscription_login_capability()`：部署不允许订阅登录（托管沙箱）时返回 403；登录、回填验证码、导入凭据三个入口共用，fail-closed。 | ✅ |
| `_account_scope.py` | 解析 | `account_env(backend, account, create=…)`：请求指名的账号 → 能到达该账号目录的环境（`None` = CLI 自己的 home）；名字或 Agent 不合法 400；账号不存在时登录、导入先建出它（别的机器同步来的配置指名的账号在本机还没有，账号在被用到时才存在），退出登录则 404（没有可登出的）；三个入口共用，对不可用的名字回答一致。 | ✅ |
| `accounts_router.py` | 路由 | 命名账号 HTTP API（`router.py` 把它挂在 `/accounts` 下）：`GET /accounts` 列出全部账号及登录态（CLI 能自报登录态的以 CLI 在该账号目录里的自报为准，否则看凭据文件，各账号并发探测），`POST /accounts` 建空账号（受 `_deploy_capability` 门控；409 已存在，400 名字或 Agent 不合法），`DELETE /accounts/{backend}/{name}` 删除账号及其登录（始终可用，托管化之前遗留的账号能被看到和删掉；先让该账号的 CLI 自己登出，登录仍在则 409 并保留账号——放在钥匙串里的登录删目录够不着，删了就再没有任何东西能移除它；删目录在线程里做）。 | ✅ |
| `router.py` | 路由 | 外部 Agent 订阅鉴权 HTTP API。`/auth/status` 强制 fresh detect（`refresh=True`）返回各 backend 安装/登录态、该 Agent 的契约通道（`transport`：`cli` 或 `acp`，设置页据此只读显示类型）与该部署是否允许订阅登录（`subscriptionLoginAllowed`，Settings badge 与表单据此取舍登录入口），并回填进程级探测缓存鲜度；不含 ephemeral RuntimePool health metrics。登录、回填、导入受 `_deploy_capability` 门控，导入的凭据存储另按同一能力位构造而拒绝写入（双层）；退出登录始终可用，使托管化之前留下的凭据能被移除，它经 `services/external_agents/logout.py` 让 CLI 自己登出再清文件，返回的是 CLI 事后自报的登录态（登录仍在就照实报告）。登录请求可带 `remote`：用户的浏览器与本服务不在同一台机器时为真，CLI 据此走在任何浏览器上都能完成的登录（Codex 走设备码，Claude 本来如此）。登录、导入、退出登录的请求体可带 `account`：作用于该账号目录里的登录（登录子进程与凭据存储都在账号的环境里），而不是 CLI 自己 home 里的；账号还不存在时登录与导入先建出它，退出登录 404。 | ✅ |
