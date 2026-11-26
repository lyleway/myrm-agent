# channels/routing/commands/router_commands/

## 架构概述

Router 命令 mixin 域：`mixin.py` 把各命令族 mixin 组合成 `RouterCommandsMixin`，各命令族一个模块。上级：[../_ARCH.md](../_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 门面 | 聚合门面，导出全部 mixin 与 `DraftAction` | ✅ |
| `mixin.py` | 核心 | 组合基类 `RouterCommandsMixin` | ✅ |
| `approval.py` / `approval_end.py` | 核心 | 审批命令与审批超时/被取代收尾；按钮点击落在被扣留回复（`outbound_draft`）或外部 Agent 的权限请求（`external_agent_permission`）上时，`resolve_approval` 之后只更新原卡片，不恢复 Agent 运行（前者已发出或释放，后者已唤醒等它的委托回合）；群聊旁观者的权限校验与其他审批一致 | ✅ |
| `delegation.py` | 核心 | `/delegate` 委派与审批交接 | ✅ |
| `draft.py` | 核心 | `/draft` 决定与 `DraftAction` | ✅ |
| `goals.py` | 核心 | `/goal`、`/subgoal`、`/background`、`/handoff` | ✅ |
| `memory.py` | 核心 | `/status`、`/kanban`、`/learn`、`/memory` | ✅ |
| `modes.py` | 核心 | `/yolo`、`/personality`、`/steer`、`/queue` | ✅ |
| `quota.py` | 核心 | `/quota` | ✅ |
| `session.py` | 核心 | `/new`、`/compact`、`/retry`、`/undo`、话题命令 | ✅ |
| `stop.py` | 核心 | `/stop` 与会话中止 | ✅ |

## 依赖关系

- `mixin.py` → `approval.py`
- `mixin.py` → `approval_end.py`
- `mixin.py` → `delegation.py`
- `mixin.py` → `draft.py`
- `mixin.py` → `goals.py`
- `mixin.py` → `memory.py`
- `mixin.py` → `modes.py`
- `mixin.py` → `quota.py`
- `mixin.py` → `session.py`
- `mixin.py` → `stop.py`
