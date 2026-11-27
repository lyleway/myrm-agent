# chat/types/

## 架构概述

聊天域 TypeScript 契约。`../types.ts` 仅 re-export 本目录；调用方继续 `@/store/chat/types`。

## 文件清单

| 文件                   | 地位 | 职责                                                                                                                                                                                                                                                | I/O/P |
| ---------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| `index.ts`             | 核心 | 子模块 barrel                                                                                                                                                                                                                                       | ✅    |
| `builtinTools.ts`      | 核心 | GUI 可切换 builtin 产品 ID                                                                                                                                                                                                                          | ✅    |
| `sources.ts`           | 核心 | 引用来源 / citation（含 KB level/path、claim evidence、`claim_status`、`claim_confidence`、`claim_text`、`snapshot_status`、`resource_uri`、`superseded_from_uri`、`source_key`、web_search 权威元数据 `site_name`/`authority_description`/`date`） | ✅    |
| `sessionConfig.ts`     | 核心 | Agent 与会话模式                                                                                                                                                                                                                                    | ✅    |
| `archiveRestore.ts`    | 辅助 | 归档恢复 payload                                                                                                                                                                                                                                    | ✅    |
| `progress.ts`          | 核心 | ProgressItem 树                                                                                                                                                                                                                                     | ✅    |
| `contextMetrics.ts`    | 辅助 | Cost / context budget                                                                                                                                                                                                                               | ✅    |
| `tokens.ts`            | 辅助 | Token 用量快照                                                                                                                                                                                                                                      | ✅    |
| `artifacts.ts`         | 核心 | 工件实体与 `ARTIFACT_TYPES`（全部工件类型；每种类型要有 `artifacts.types.<type>` 文案）                                                                                                                                                             | ✅    |
| `toolApproval.ts`      | 核心 | 工具审批                                                                                                                                                                                                                                            | ✅    |
| `sessionAccess.ts`     | 核心 | 会话目录授权根 `SessionAccessRoot` 与 API JSON → 根列表的解析；路径原样保留（撤销时原样发回，名字以空格结尾的文件夹不是没有空格的那个），只有展示文本去空白                                                                                         | ✅    |
| `agentStream/part1.ts` | 核心 | SSE 事件（前段）                                                                                                                                                                                                                                    | ✅    |
| `agentStream/part2.ts` | 核心 | SSE 事件（中段）                                                                                                                                                                                                                                    | ✅    |
| `agentStream/part3.ts` | 核心 | SSE 事件（末段）                                                                                                                                                                                                                                    | ✅    |
| `agentStream/union.ts` | 核心 | `AgentStreamEvent` union                                                                                                                                                                                                                            | ✅    |
| `messages.ts`          | 核心 | `Message`（含后台完成通知种类 `WakeupNoticeKind`）/ 历史 / @mention / 附件 `File`（`mediaId` 指向媒体库条目，发给模型时以 `media://<id>` 引用）                                                                                                     | ✅    |
| `chatState.ts`         | 核心 | `ChatState` + actions                                                                                                                                                                                                                               | ✅    |

## 依赖

- `@/store/config/providerTypes` — 模型选择
- `@/components/features/chat-window/goals/GoalStatusCard` — `GoalStatusPayload.status`（`part2.ts`）

## 约束

- 单文件不超过 500 行；新增类型写入对应域文件。
- `messageStream/types.ts` 为本目录外的流 handler 专用类型，勿合并进此包。
