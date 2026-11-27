# services/artifact_apps/

## 架构概述

产物应用的业务层。HTML 产物是不受信的生成代码，用户「安装为应用」才允许其页面经宿主请求能力（通知、受控网络访问，以及只在应用自己的桌面窗口里存在的托盘项、全局快捷键、浮窗置顶）。授权按 `(artifact_id, capability)` 保存「批准的范围 + 批准时的版本 sha」；新版本声明了未批准的范围时由宿主重新询问。活动日志只记元数据（能力、结果、主机名、版本），不记 URL 路径、请求体与响应体。上级文档：[../_ARCH.md](../_ARCH.md)；HTTP 层在 [api/artifact_apps/](../../api/artifact_apps/_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | 包说明 | — |
| `capabilities.py` | 核心 | 能力/决定/结果词表与范围规范化：`notify` / `tray` / `float` 无范围，`fetch` 为精确公网主机名列表（无通配、无 IP 字面量、无端口），`shortcut` 为规范拼写的全局组合键列表（至少一个 Shift 之外的修饰键，键为 A-Z / 0-9 / F1-F12，至多 4 个；与前端 `accelerator.ts`、桌面壳 `accelerator.rs` 是同一条规则）；服务端只存决定与范围，三项桌面能力的执行在桌面壳 | ✅ |
| `store.py` | 核心 | 三张表的唯一读写入口：安装登记、授权 upsert、`allowed_fetch_hosts`、活动写入与按应用保留最近 200 条；每次写入都先结束调用方留下的读事务再取写锁（`_begin_write`）：路由先读（是否已安装、允许哪些主机）、代理请求夹在中间，留着读快照会让写入在别的请求提交之后以 `database is locked` 失败（busy_timeout 不覆盖），成功的代理请求就会回错误、活动记录丢行 | ✅ |
| `fetch_proxy.py` | 核心 | 宿主 fetch 代理：主机白名单、方法与头部限制、请求/响应体上限、超时；经 `secure_request`（SSRF 防护开启、禁止重定向）出站；剥离响应 Cookie | ✅ |
| `rate_limit.py` | 辅助 | 进程内滑动窗口 + 并发上限（`FETCH_LIMITER`：60 次/分钟、同时 2 个），只约束突发 | ✅ |

## 依赖关系

- `app.database.models.artifact_app`（三张表）、`app.database.models.artifact`（列表取名称）
- `myrm_agent_harness.core.security.http.secure_fetch::secure_request`（SSRF 安全出站原语）
