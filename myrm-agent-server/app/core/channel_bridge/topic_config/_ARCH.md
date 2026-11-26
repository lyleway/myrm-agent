# app/core/channel_bridge/topic_config 子包架构

渠道 Topic 配置域。`SqlTopicManager` 由四个 mixin 组合而成，每个 mixin 负责一类操作；共享常量与 helper 在 `common.py`，对存储 map 的纯操作在 `topic_map.py`。`app.core.channel_bridge.topic_config` 导入路径不变。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 子域出口 | 聚合 `SqlTopicManager`、`_CHANNEL_LEVEL_KEY` 与 search-bind 错误 helper | — |
| `common.py` | 辅助 | 共享常量（缓存 TTL、默认空闲超时、`_UNSET` 类型化哨兵、消息文案）、渠道写代数（`bump_write_generation` / `write_generation`）、logger、`clean_identity_id` / `is_search_agent_channel_bind_error` | ✅ |
| `topic_map.py` | 辅助 | 存储 map `{chat_id: {storage_key: entry}}` 上的纯操作：`storage_key_of` / `binding_entry` / `delete_binding` / `drop_bindings_of`；无 I/O、无 await，可在写锁内直接作用于锁内读出的 map | ✅ |
| `manager.py` | 核心 | `SqlTopicManager(BindingMixin, LifecycleMixin, PolicyMixin, ConfigStoreMixin)` + `__init__`（读缓存、已读写代数、活跃写入时间与默认超时） | ✅ |
| `binding.py` | 核心 | `resolve_topic` / `bind_topic` / `unbind_topic` / `sync_topic_metadata` 与 `topic_context`：解析、绑定（拒绝 search agent、workspace 互斥）、解绑与元数据同步；需要外部世界的校验在取锁前完成，锁内只合并变更 | ✅ |
| `lifecycle.py` | 核心 | 绑定的生命周期：过期判定、活跃时间刷新（5 分钟节流、不复活已删绑定）、从全部绑定中移除 Agent；建立在 `ConfigStoreMixin` 之上 | ✅ |
| `policy.py` | 核心 | 读路径（`get_all_topics` 清掉不可绑定 Agent 的绑定）、Agent ID 解析、渠道可绑定性校验与 search-track 判定 | ✅ |
| `config_store.py` | 核心 | UserConfig 中 topic map 的读写：唯一写入口 `_mutate_config`（取写锁、读锁内当前 map、对副本执行变更、提交）、带读缓存的 `_load_config`、`_stored_channels` | ✅ |

## 写入与缓存一致性

所有写入只走 `ConfigStoreMixin._mutate_config`：取 SQLite 写锁，再读出库中当前的 map，深拷贝后交给调用方给出的纯同步变更函数，函数返回 `None` 表示无需改动（不写库），否则提交。变更函数基于锁内读到的最新 map 决策，而不是调用方早先读到的副本，所以两个 manager 同时编辑同一渠道的不同绑定，谁也不会覆盖谁；同一条绑定的并发编辑逐个排队执行。

写失败的处理分两类：用户动作（绑定、解绑、元数据同步）直接抛出，调用方得到真实的失败；后台维护（过期与不可绑定绑定的清理、活跃时间刷新、Agent 级联删除）尽力而为，失败只记 warning，并在锁内对库中当前条目重新判定（`only_if`），不会删掉别处刚刚重绑的新绑定。

每个 `SqlTopicManager` 实例各有读缓存（TTL 60 s）：路由器持有长生命周期实例，API 与命令按调用新建实例。提交成功后渠道的写代数加一，当前实例丢弃缓存；其余实例在下一次读取时发现代数变化，丢弃自己的缓存副本，因此各处读到同一份最新配置。每条入站消息都会调用 `sync_topic_metadata`，它先用缓存读判断「名称与头像无变化」，只有存在差异时才取写锁。写代数只在进程内共享，服务端按单进程部署。

## 依赖关系

- 上游：`app.channels.types`（`TopicContext` / `IdentityScopeMode` / `ReplyMode` …）
- 下游：`app/core/channel_bridge/setup/`、`app/api/channels/topics.py`、`app/core/cron/adapters/*`
