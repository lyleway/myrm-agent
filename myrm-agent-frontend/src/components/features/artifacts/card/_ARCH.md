# card/ 模块架构

## 架构概述

聊天消息里一张工件卡片的组成部分：头部、动作行、内联预览、向下展开的面板、发布状态条，以及驱动它们的状态与动作。`../ArtifactCard.tsx` 只把这些部分拼起来，并持有它们共享的两件事：哪些区块展开着、点击卡片预览哪个工件。

## 文件清单

| 文件                              | 地位 | 职责                                                                                                                                                                                | I/O/P |
| --------------------------------- | ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| `ArtifactCardHeader.tsx`          | 核心 | 卡片左侧：类型图标、文件名、类型与大小、落在本机磁盘上的文件路径                                                                                                                    | ✅    |
| `ArtifactCardActions.tsx`         | 核心 | 卡片右侧动作行：显示哪些按钮只取决于工件类型、是否有本地路径与发布状态；固钉到项目工作台在此处理                                                                                    | ✅    |
| `IconAction.tsx`                  | 辅助 | 动作行里的一个图标按钮；点击不冒泡到卡片自身的点击预览                                                                                                                              | ✅    |
| `ArtifactInlinePreview.tsx`       | 核心 | 卡片下方可展开的可视预览，四种状态：大文件提示、加载中、渲染内容、读取失败                                                                                                          | ✅    |
| `ArtifactCardPanels.tsx`          | 核心 | 向下展开的面板：HTML 工件的公众号草稿、整理计划编辑；常驻挂载，已读到的计划在面板关闭后保留                                                                                         | ✅    |
| `ArtifactPublicationBanners.tsx`  | 辅助 | 卡片头下方的发布状态条：已发布链接与「副本落后于最新版本」的重新发布提示                                                                                                            | ✅    |
| `ArtifactSharePopover.tsx`        | 核心 | 卡片动作：创建只读分享短链（可选密码与有效期）并复制                                                                                                                                | ✅    |
| `ArtifactLocalFileActions.tsx`    | 辅助 | 落在磁盘上的工件的卡片动作：用默认应用打开、在文件管理器中显示、复制路径                                                                                                            | ✅    |
| `ArtifactAppButton.tsx`           | 核心 | 卡片动作「安装为应用」：HTML 工件未安装时先登记再打开，已安装时直接打开（桌面端开独立窗口，其余走 `/apps/[artifactId]`）；失败提示后保持可再点；点击不冒泡到卡片                    | ✅    |
| `artifactKinds.ts`                | 辅助 | 每种工件类型能提供什么（预览、复制、内联预览、写入知识库）与类型配色，一处查表                                                                                                      | ✅    |
| `useArtifactPublishing.ts`        | 核心 | 卡片对发布的全部认知：服务端最近给出的工件、发布记录、能否部署（preflight）与发布对话框状态；父级传入新工件时重新开始；preflight 读不到按「不可部署」处理                            | ✅    |
| `useArtifactContent.ts`           | 核心 | 工件文本的带缓存读取（内联预览、复制、草稿与计划面板共用），以及悬停预热：大文件不预热，卡片在悬停中卸载时不再发起读取                                                              | ✅    |
| `useArtifactActions.ts`           | 核心 | 一次性动作：复制、新标签打开、写入知识库、插入对话附件、下载                                                                                                                        | ✅    |
| `useLazyText.ts`                  | 辅助 | 启用时才读一次文本；读不到算失败，在保持启用期间不重试，关闭再打开才重试；已读到的文本在关闭后保留                                                                                  | ✅    |
| `__tests__/useLazyText.test.ts`   | 测试 | 何时读取、失败何时重试、关闭后保留什么、关闭后才到的结果被丢弃                                                                                                                      | ✅    |
| `__tests__/ArtifactAppButton.test.tsx` | 测试 | 先登记再打开、已装只打开、失败提示且不打开、点击不冒泡、登记表只在未读过时读取                                                                                                    | ✅    |

## 依赖

- `../artifactUtils` — preflight、发布记录比对与回写、下载文件名、图标
- `../PublishModal`、`../WeChatDraftPanel`、`../OrganizePlanPanel`、`../renderers/*` — 对话框、面板与预览渲染
- `@/store/useArtifactPortalStore` — 工件文本的共享缓存
- `@/components/features/dashboard-workspace/useDashboardWorkspaceStore` — 固钉到项目工作台

## 约束

- 动作行的按钮显示规则只在 `artifactKinds` 与 `ArtifactCardActions` 内判定，卡片本体不再按类型分支
- 异步读取的结果在消费者关闭或卸载后丢弃，不写回状态
