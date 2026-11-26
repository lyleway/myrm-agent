# api/external_agents/

## 架构概述

外部 Agent 连接档案 HTTP 层。上级文档：[../_ARCH.md](../_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | External delegated agent auth endpoints. | ✅ |
| `_deploy_capability.py` | 门控 | `require_subscription_login_capability()`：部署不允许订阅登录（托管沙箱）时返回 403；登录、回填验证码、导入凭据三个入口共用，fail-closed。 | ✅ |
| `router.py` | 路由 | 外部 Agent 订阅鉴权 HTTP API。`/auth/status` 强制 fresh detect（`refresh=True`）返回各 backend 安装/登录态、该 Agent 的契约通道（`transport`：`cli` 或 `acp`，设置页据此只读显示类型）与该部署是否允许订阅登录（`subscriptionLoginAllowed`，Settings badge 与表单据此取舍登录入口），并回填进程级探测缓存鲜度；不含 ephemeral RuntimePool health metrics。登录、回填、导入受 `_deploy_capability` 门控，导入的凭据存储另按同一能力位构造而拒绝写入（双层）；退出登录始终可用，使托管化之前留下的凭据能被移除。 | ✅ |
