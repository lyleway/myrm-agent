# services/artifact_apps/

## 架构概述

产物应用的业务层。HTML 产物是不受信的生成代码，用户「安装为应用」才允许其页面经宿主请求能力（通知、受控网络访问、用你配置的模型做一次补全，以及只在应用自己的桌面窗口里存在的托盘项、全局快捷键、浮窗置顶）。授权按 `(artifact_id, capability)` 保存「批准的范围 + 批准时的版本 sha」；新版本声明了未批准的范围时由宿主重新询问。活动日志只记元数据（能力、结果、主机名或花费、版本），不记 URL 路径、请求体、响应体与提示词。上级文档：[../_ARCH.md](../_ARCH.md)；HTTP 层在 [api/artifact_apps/](../../api/artifact_apps/_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | 包说明 | — |
| `capabilities.py` | 核心 | 能力/决定/结果词表与范围规范化：`notify` / `ai` / `tray` / `float` 无范围，`fetch` 为精确公网主机名列表（无通配、无 IP 字面量、无端口），`shortcut` 为规范拼写的全局组合键列表（至少一个 Shift 之外的修饰键，键为 A-Z / 0-9 / F1-F12，至多 4 个；与前端 `accelerator.ts`、桌面壳 `accelerator.rs` 是同一条规则）；服务端只存决定与范围，三项桌面能力的执行在桌面壳 | ✅ |
| `store.py` | 核心 | 登记、授权、活动三张表的唯一读写入口：安装登记（卸载连带授权与活动，但不动花费账本）、授权 upsert、`is_allowed`、`allowed_fetch_hosts`、活动写入与按应用保留最近 200 条；每次写入都先结束调用方留下的读事务再取写锁（`begin_write`，账本复用）：路由先读（是否已安装、允许哪些主机）、代理请求夹在中间，留着读快照会让写入在别的请求提交之后以 `database is locked` 失败（busy_timeout 不覆盖），成功的代理请求就会回错误、活动记录丢行 | ✅ |
| `fetch_proxy.py` | 核心 | 宿主 fetch 代理：主机白名单、方法与头部限制、请求/响应体上限、超时；经 `secure_request`（SSRF 防护开启、禁止重定向）出站；剥离响应 Cookie | ✅ |
| `ai_usage.py` | 核心 | `ai` 的花费账本（`artifact_app_ai_usage`）：`reserve` 在同一个写事务里检查应用当日上限并按调用上界先记一笔，所以并发调用不会一起越过各自都守得住的上限；`settle` 改记实际花费；`spent_since` 按应用或全部汇总。卸载应用不删账本（钱已经花了，重装也不能让它的一天重新开始），超过 8 天的行在写入时清理 | ✅ |
| `ai_proxy.py` | 核心 | 宿主模型调用代理：用用户配置的模型做一次不带工具、不带对话的纯补全，只用页面给的文字。花费三重封顶：用户总预算（应用调用不使用智能体收尾用的预留，每次调用都计入当日）、每个应用每日 $1、单次调用按「提示字节数 + 输出上限」估出的上界先记账；没有已知价格的模型直接拒绝（成本算不出来就无从封顶）。失败、超时、取消都保留预留的那一笔；完成后按请求级 token 追踪器累计的实际花费改记（含适配器因空回复重试的每一次） | ✅ |
| `rate_limit.py` | 辅助 | 进程内滑动窗口 + 并发上限（`FETCH_LIMITER`：60 次/分钟、`AI_LIMITER`：20 次/分钟，各同时 2 个），只约束突发 | ✅ |

## 依赖关系

- `app.database.models.artifact_app`（登记、授权、活动、花费账本四张表）、`app.database.models.artifact`（列表取名称）
- `app.services.budget.enforcer`（用户总预算判定与进程级守卫）、`app.services.budget.budget_day`（预算日起点）；`myrm_agent_harness.utils.token_economics`（价格表与请求级追踪器）
- `myrm_agent_harness.core.security.http.secure_fetch::secure_request`（SSRF 安全出站原语）
