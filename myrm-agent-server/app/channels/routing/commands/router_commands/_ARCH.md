# channels/routing/commands/router_commands/

## 架构概述

Router 命令 mixin 域：`mixin.py` 把各命令族 mixin 组合成 `RouterCommandsMixin`，各命令族一个模块。上级：[../_ARCH.md](../_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 门面 | 聚合门面，导出全部 mixin 与 `DraftAction` | ✅ |
| `mixin.py` | 核心 | 组合基类 `RouterCommandsMixin` | ✅ |
| `approval.py` / `approval_end.py` | 核心 | 审批命令与审批超时/被取代收尾；按钮点击落在被扣留回复（`outbound_draft`）或外部 Agent 的权限请求（`external_agent_permission`）上时，`resolve_approval` 之后只更新原卡片，不恢复 Agent 运行（前者已发出或释放，后者已唤醒等它的委托回合）；谁能作答由 `_may_decide` 一条规则裁定，文字回答、反应与按钮点击共用，都对照聊天里等待的那条审批请求（提问人取自它；请求或回答任一方说是群聊就按群聊算，因为群聊标志只在说「是」时才算证据，Telegram 把所有按钮点击都报成私聊）：私聊谁发的都算，群聊只有发起审批的成员；聊天没有等待中的请求时，按钮点击只有 owner 能决定，已定案的审批不校验点击者、只把卡片改成已处理；文字回答与反应在取走审批之前判断，按钮点击恢复运行前再确认等待的仍是判断时的那条请求；被拒的回答记 INFO 日志后丢弃、不回复，审批保持待答 | ✅ |
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
