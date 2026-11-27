# api/artifact_apps/

## 架构概述

已安装产物应用的 HTTP 层：安装登记、按能力授权、桥接活动日志、宿主 fetch 代理与模型调用代理。只有应用宿主页调用这些端点，沙箱内的页面自身没有网络。上级文档：[../_ARCH.md](../_ARCH.md)；业务逻辑在 [services/artifact_apps/](../../services/artifact_apps/_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | 导出 `router` | — |
| `router.py` | 核心 | 安装（仅 HTML 产物）/卸载/列表/详情、授权写入、活动日志读写、`POST /{artifact_id}/fetch` 与 `POST /{artifact_id}/ai` 代理入口（后者先查授权与限速，再把提示词与系统文字交给风控闸门 `reject_if_risky`（被拦返回 400 `risk_held` 并记入活动日志），最后取用户配置的模型交给 `run_ai`；拒绝与失败都以稳定的 `code` 回给宿主并记入活动日志，详情只写花费或拒绝原因，不写提示词） | ✅ |
| `schemas.py` | 辅助 | 请求/响应模型；字段上限与服务层一致，非法请求在此以校验错误失败 | ✅ |

## 依赖关系

- `app.services.artifact_apps`（安装表、授权、活动、代理）
- `app.core.artifacts.listener::ensure_artifact_for_deploy`（加载或即时登记产物）
