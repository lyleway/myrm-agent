# compact/ — Chat context compaction implementation

## 架构概述

Server 业务层无损上下文压缩实现。`compact_service.py` 为对外 facade；本子包按职责拆分持久化、改写历史后的摘要结算、idle 估算、消息 IO、summarize 超时守卫与 `compact_chat` 入口。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
| --- | --- | --- | --- |
| `_constants.py` | 辅助 | 压缩/idle 阈值常量 | — |
| `_types.py` | 核心 | `CompactResult`、`CompactReason`（没压缩时的原因码闭集；Web 与 IM 按原码取文案，码值是线上契约） | ✅ |
| `_lock.py` | 辅助 | 每 chat 并发锁 | ✅ |
| `service.py` | 核心 | `compact_chat`（circuit + anti-thrash + idle stale；`request_tokens_for_guard` 对齐 gate 口径；人工请求 `explicit_request` 不经 anti-thrash 守卫，结果计入无效 streak 但至多计到守卫上限；省下的 token = 旧摘要 + 其后消息 − 新摘要，下限 0，这一口径由 `tokens_replaced_by_summary` 给出、空闲 drain 共用，累计统计不会把旧摘要已覆盖的行再记一遍；没压缩只回裸原因码，细节进日志；事务边界：先读、提交结束读快照、再调模型（秒到分钟级，期间不持有任何数据库快照，否则别处任一提交都会让随后的写以 `SQLITE_BUSY_SNAPSHOT` 失败、摘要白付）；落库交给 `persist.do_persist_to_db`，提交后会话不再有未提交的写（连续无效次数由守卫记录函数经存储自带的连接写入）；模型失败与超时的冷却立即提交，会话不提交就关闭会回滚它） | ✅ |
| `persist.py` | 核心 | DB 持久化 + failure cooldown；`do_persist_to_db` 先读压缩边界再写，起手取写锁（手动压缩、Pipeline 回调 `persist_compaction` 与空闲 drain 三条路径共用，否则读与写之间别处一次提交就让落库以 `SQLITE_BUSY_SNAPSHOT` 失败）；调用方在模型调用之前选定的边界必须仍是该聊天里的活跃消息（没给边界取最新一条活跃消息），被删除、或被重新生成 / 兄弟切换 / Focus & Flush 停用都抛 `ValueError`，不存指向已不在提示词里的消息的摘要 | ✅ |
| `history_change.py` | 核心 | `settle_compaction_after_history_change`：重试、重新生成、兄弟切换、撤销、截断与 rewind 在各自的事务里调用；摘要写入时间不早于变更点（或没有写入时间）就清空 `compacted_summary` / `compacted_before_id` / `compacted_at`，更早写入的摘要仍有效而保留；累计统计 `compacted_tokens_saved` 不清；删了行时重建会话召回索引（文档层只增不删，不重建就留着已删消息的原文） | ✅ |
| `compression_streak.py` | 核心 | Chat DB anti-thrash streak + harness store registration；写经存储自带的连接（守卫 `record_compression_effectiveness` 调 `set_streak`），异步 ORM 路径只读（`load_`/`hydrate_`），不在请求会话里留未提交的写 | ✅ |
| `idle_estimate.py` | 核心 | idle gate token 估算与 floor | ✅ |
| `message_io.py` | 辅助 | 消息加载/备份/LC 转换（压缩只读活动分支：被重新生成或兄弟切换停用的回复不进摘要）；`parse_existing_summary` 委托 harness `parse_structured_summary_json`（14 字段全量反序列化，失败 None） | ✅ |
| `summarize_guard.py` | 辅助 | `/compact` 路径 progress 超时 | ✅ |
| `llm_config.py` | 辅助 | 用户模型 LLM 解析 | ✅ |
| `archive.py` | 辅助 | workspace 备份读取 | ✅ |

## 依赖

- harness：`summarize_circuit_guard`、`compression_anti_thrash_guard`、`generate_structured_summary`、`summary_parser.parse_structured_summary_json`（compacted_summary 全字段反序列化）
- server：`Chat`/`Message` ORM、`ConversationRecallIndexService`、`core.utils.time_utils.as_utc`（SQLite 读回的时间补 UTC 后再比较）
