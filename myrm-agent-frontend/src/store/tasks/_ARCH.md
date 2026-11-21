# store/tasks/ 模块架构

## 架构概述

后台任务的类型契约；目录内没有 store 与运行时代码。任务状态与 SSE 订阅见 `hooks/tasks/`。

## 文件清单

| 文件       | 职责                                                                                                                                                                                                                                                                          |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `types.ts` | `Task`（任务记录：状态、载荷、结果、错误、进度与时间戳）、`TaskStatus`（六种状态）、`TaskError`（`recoverable` 区分瞬时与永久失败）与图像生成任务的结果形状 `ImageGenerationResult`（`provider` 可选：任务结果不带供应商，见 `shared/config/media_generation_contract.json`） |

## 依赖

- 无：`types.ts` 不 import 任何模块
- 被 `hooks/tasks/`、`services/mediaTasks.ts`、`components/features/task-card/`、`components/features/background-tasks/` 引用
