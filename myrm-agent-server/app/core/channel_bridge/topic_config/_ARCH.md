# app/core/channel_bridge/topic_config 子包架构

渠道 Topic 配置域。`SqlTopicManager` 由四个 mixin 组合而成，每个 mixin 负责一类操作；共享常量与 helper 在 `common.py`。`app.core.channel_bridge.topic_config` 导入路径不变。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 子域出口 | 聚合 `SqlTopicManager`、`_CHANNEL_LEVEL_KEY` 与 search-bind 错误 helper | — |
| `common.py` | 辅助 | 共享常量（缓存 TTL、默认空闲超时、`_UNSET`、消息文案）、渠道写代数（`bump_write_generation` / `write_generation`）、logger、`clean_identity_id` / `is_search_agent_channel_bind_error` | ✅ |
| `manager.py` | 核心 | `SqlTopicManager(BindingMixin, IdentityMixin, PolicyMixin, ConfigStoreMixin)` + `__init__`（读缓存、已读写代数与默认超时） | ✅ |
| `binding.py` | 核心 | `resolve_topic` / `bind_topic` / `unbind_topic` / `sync_topic_metadata`：解析、绑定（拒绝 search agent、workspace 互斥）、解绑与元数据同步 | ✅ |
| `identity.py` | 核心 | 身份撤销/恢复、过期判定、活跃时间刷新、从全部绑定中移除 Agent（读前先取写锁，改动写入副本）；建立在 `ConfigStoreMixin` 之上 | ✅ |
| `policy.py` | 核心 | 读路径、Agent ID 解析、绑定键删除、渠道可绑定性校验与 search-track 判定 | ✅ |
| `config_store.py` | 核心 | UserConfig 中 topic map 的读写：保存先取写锁再读；每次保存递增渠道写代数，各 manager 的读缓存据此失效 | ✅ |

## 缓存一致性

每个 `SqlTopicManager` 实例各有读缓存（TTL 60 s）：路由器持有长生命周期实例，API 与命令按调用新建实例。任一实例保存后，渠道的写代数加一；其余实例在下一次读取时发现代数变化，丢弃自己的缓存副本，因此各处读到同一份最新配置，过期副本也不会被「最近活跃时间」之类的回写覆盖掉别处的编辑。保存先取 SQLite 写锁再读（`acquire_write_lock`），并发写入方排队等待，不会因 `database is locked` 丢失更新。读改写本身不是原子的：同一渠道上两次完全重叠的编辑，后保存者覆盖先保存者。写代数只在进程内共享，服务端按单进程部署。

## 依赖关系

- 上游：`app.channels.types`（`TopicContext` / `IdentityScopeMode` / `ReplyMode` …）
- 下游：`app/core/channel_bridge/setup/`、`app/api/channels/topics.py`、`app/core/cron/adapters/*`
