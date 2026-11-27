# ai_agents/extensions/

## 架构概述

AgentExtension 具体实现（安全/子 Agent/任务自适应等）。上级文档：[../_ARCH.md](../_ARCH.md)。

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| `__init__.py` | 入口 | 包入口与导出 | — |
| `archive_checkpoint_memory.py` | 模块 | Persist pruned tool-output summaries and emit ledger / SSE notifications. | ✅ |
| `extraction_lifecycle.py` | 模块 | Harness `auto_extract_memories` lifecycle observer → ledger + SSE；支持 `source` / `is_retry` metadata；`source=auto_extract_memories` 且非重试（`is_retry=False`）的提取失败自动入持久化重试队列（防无限循环：手动重试/worker 失败不再次入队） | ✅ |
| `pre_compact_memory.py` | 模块 | Inject semantic memory recall before context compaction and record ledger events. | ✅ |
| `security_policy_extension.py` | 模块 | Extension that configures the agent's security policies and PII handling. `sync_wrapper_security_from_store` 每轮重载已保存的 securityConfig，并把本请求的 `SecurityOverlaySpec`（会话预设 / 会话授权（YOLO、审批窗口）/ 远程准入 / learn 标记）重放在其上，已保存配置不会放宽、也不会冲掉这些覆盖；预设与远程准入是追加在重载副本上的限制层，每轮只有一份；返回重载到的已保存配置（叠加覆盖之前，没有保存过则为 `None`）。`sync_wrapper_security_inputs` 是每个要判工具调用的回合的起点：先按 Agent 档案重读它自己的覆盖，再调 `sync_wrapper_security_from_store`；普通回合（`stream_pipeline`）与用户点名外部 Agent 的直连回合（`forced_delegate`）共用，设置里的改动对下一条消息就生效。 | ✅ |
| `subagent_extension.py` | 模块 | Registers subagent delegation tools on ``agent._tool_registry`` in ``on_agent_init`` (before first ``create_agent``); forwards the chat's ``incognito_mode`` and the turn's ``credential_track`` to the subagent catalog. | ✅ |
| `zero_cost_memory.py` | 模块 | Extension that intercepts evicted tool calls/responses from the ContextPipeline, auto-extracts long-term memories in the background, and publishes `MEMORY_OPERATION` SSE events. Deep PII scan is resolved from the live `PrivacyPolicy` at extraction time (pooled-agent reuse safe). Integrated with sandbox write gate to skip tool eviction extraction for sandbox-capable agents, and short-circuits eviction extraction when `memory_policy.allow_l3_extraction` is False. | ✅ |
