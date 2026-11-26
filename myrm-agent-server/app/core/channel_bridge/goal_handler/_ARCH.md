# core/channel_bridge/goal_handler/

## 架构概述

IM 渠道的 `/goal` 与 `/subgoal` 命令处理器：把渠道消息解析到所属 Web 聊天，再经 `GoalRegistry` 的 provider 操作目标。
读写目标状态（含创建）一律经 `GoalRegistry.resolve_provider`：没有回合在跑（没有注册 provider）的聊天也能操作存储里的目标；命令处理不登记 provider，注册表只放运行中回合的句柄。
`__init__.py` 只导出 `ChannelGoalCommandHandler`，对外 import 路径保持 `app.core.channel_bridge.goal_handler`。
上级文档：[../_ARCH.md](../_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | 导出 `ChannelGoalCommandHandler` | ✅ |
| `handler.py` | 核心 | `ChannelGoalCommandHandler`：创建目标时注入 source channel metadata（channel/chat_id/thread_id/locale）供 `GoalTerminalNotifier` 闭环回推；`/goal clear` 与 Web 取消共用 `select_cancellable_goal`（暂停/预算受限/待复核/等待中的目标也能清除）；`/goal pause`/`/goal clear` 与 Web 一样中断正在干活的目标的回合（`stop_goal_turn`）；`/goal resume` 先等「已被要求停止」的回合收尾（`wait_for_stopped_turn`，等不到回复 `goal_resume_stopping` 且目标保持暂停），再恢复并经 `get_resume_kickoff_message` 让目标回到工作；`/goal unwait` 结束等待后同样经 `get_resume_kickoff_message` 让目标回到工作（与 Web unwait 一致）；除创建目标外的命令与 `get_resume_kickoff_message` 都经 `resolve_provider` 取 provider，回合之间也作用于存储里的目标，聊天里没有目标时各命令回自己的「无目标」文案；`/goal <目标>` 是唯一会建聊天的命令（首次交互时目标没有聊天可挂，建出聊天再存目标，随后的 kickoff 回合就在这个聊天里干活），其余命令只查不建；`/new` 之后目标命令作用于新会话（`/new` 已把它落库，`/goal <目标>` 把目标存在新会话上），已结束会话里的目标不受影响；全部静态回复走 channel i18n catalog | ✅ |
| `status_view.py` | 辅助 | `render_goal_status`：`/goal status` 的多行本地化回复（状态、预算、约束、验收标准、子目标），纯展示，不做 provider 查询 | ✅ |
| `chat_resolution.py` | 辅助 | `resolve_chat_id(msg, *, create=False)`：渠道消息 → 它所在会话的聊天 id，用 `command_session.resolve_command_session_key` 解出的会话键（与 `/undo`、执行器存下一回合的会话同一把：服务该消息的智能体、重置策略的当前周期），按该键精确查；尚无聊天返回 None，`create=True`（`/goal <目标>`）则建出执行器首个回合会用的那个聊天 | ✅ |

## 测试

- `tests/core/channel_bridge/test_goal_resume_command.py` — `/goal resume` 等回合收尾、等不到时拒绝并保持暂停、不被无人叫停的在途回合拖住；`resolve_chat_id` 的 patch 点是 `goal_handler.handler.resolve_chat_id`
- `tests/core/channel_bridge/test_goal_stop_commands.py` — `/goal clear` / `/goal pause` 与 Web 取消同一批目标、同一次中断
- `tests/core/channel_bridge/test_goal_commands_between_turns.py` — 回合之间（没有注册 provider）`/goal status|pause|resume|clear|wait|unwait|budget|constraint` 与恢复 kickoff 作用于存储里的目标；聊天里没有目标时各命令回自己的文案，且不注册 provider
- `tests/core/channel_bridge/test_goal_subgoal_command.py` — 回合之间 `/subgoal add` 落在存储里的目标上，且不注册 provider
- `tests/core/channel_bridge/test_goal_chat_resolution.py` — 真实库：目标命令作用的聊天就是执行器写入的那个（无绑定、聊天绑定点名智能体、身份冻结），每日重置之后昨天的聊天不再是这个会话，首次交互时设的目标存在首个回合会用的聊天上，不设目标的命令不建聊天，`/new` 之后目标命令作用于新会话、不碰已结束会话里的目标
- `tests/core/channel_bridge/command_session_testkit.py` — 解出会话键所读的三处存储（话题绑定、个人设置、智能体档案）的替身，与 `test_command_session.py` 共用
- `tests/core/channel_bridge/test_goal_status_view.py` — `/goal status` 回复的各部分与顺序
- `tests/channels/test_goal_command.py` — 路由层对 handler 协议的调用
