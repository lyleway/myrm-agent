# settings/sections/integration 模块架构

## 架构概述

设置页「集成」域 Section 组件：凭证、外部 Agent 连接、浏览器扩展桥、集成目录与通信渠道容器。`CommunicationSection` 以 Tab 聚合 `channels/` 子目录。

## 文件清单

| 文件                                                         | 地位                                                                           | 职责                                          | I/O/P |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------ | --------------------------------------------- | ----- |
| `ConnectSection.tsx`                                         | 核心                                                                           | Connect Wizard                                | ✅    |
| `ExtensionBridgeSection.tsx`                                 | 核心                                                                           | 浏览器扩展桥 UI                               | ✅    |
| `extension/ExtensionClipAgentField.tsx`                      | 组件                                                                           | Wiki 剪藏目标 Agent 下拉                      | ✅    |
| `extension/`                                                 | 子模块                                                                         | 见 [`extension/_ARCH.md`](extension/_ARCH.md) | ✅    |
| `CredentialsSection.tsx`                                     | re-export                                                                      | 凭证管理                                      | —     |
| `credentials/`                                               | Vault / 文件 / OAuth 凭证子模块 · [credentials/_ARCH.md](credentials/_ARCH.md) |
| `ExternalAgentsConfig.tsx`                                   | 外部 Agent 列表：启停、删除、持久化（`externalAgents` 配置键）；保存前校验名称、命令与环境变量；命令恰好是已知 Agent 时，图标与保存的类型取服务端报告的契约通道 |
| `ExternalAgentForm.tsx`                                      | 外部 Agent 编辑表单（预设、类型、命令、权限、鉴权方式、环境变量、测试）；已知 Agent 的类型只读显示并附一句说明；托管部署只给 API 密钥一个选项并换成对应说明；权限说明随命令与生效的类型变化（会询问 / 只能读取 / 无法限制） |
| `ExternalAgentEnvEditor.tsx`                                 | 环境变量逐行编辑：值默认掩码、可显示；行内提示名称缺失/非法/重复；随鉴权方式给出说明          |
| `externalAgentModel.ts`                                      | 纯规则：预设、权限模式（只有 `safe` 与 `allow_all`，旧词表里存下来的值归为 `safe`）与权限说明 `permissionHintKey`（Claude Code 与 ACP 会逐条询问；Codex 的命令行不能询问，`safe` 即只读；未知命令没有可设的参数）、`knownBackendOfCommand`（命令恰好是哪个已知 Agent，认可执行文件名本身）与 `contractTransport`（该 Agent 的契约通道，取自服务端状态）、预设（Gemini、Qwen 为 ACP 且不带参数）、`toDraft`/`cleanAgent`（环境变量 ↔ 编辑行；部署不允许订阅登录时存为 API 密钥；契约通道优先于旧值）、`effectiveAuthMode`、`envRowProblems` |
| `ExternalAgentAuthControls.tsx`                              | 外部 Agent 单行控件：CLI 缺失时给出安装入口，已安装时给出登录状态徽标（取 CLI 自述的登录态）与登录 / 退出登录；安装对话框常驻挂载，状态翻转为已安装也不打断成功提示 |
| `ExternalAgentInstallDialog.tsx`                             | 安装对话框：打开即安装并流式显示安装日志；成功即刷新状态再自行关闭；失败可重试；取消 / 关闭 / 卸载都会中止请求，服务端随之停掉 npm |
| `ExternalAgentLoginDialog.tsx`                               | 登录对话框：脚本化登录事件流（设备码 / 浏览器授权 / 验证码回填）+ 凭据导入兜底                                                |
| `OpenAIApiSection.tsx`                                       | Agent API 设置（OpenAI 兼容端点，仅 Agent 执行）                               |
| `CommunicationSection.tsx`                                   | 渠道 Tab 容器（聚合 `channels/`）                                              |
| `integrations/`                                              | Integration Catalog、Lifecycle Webhooks、连接对话框、记忆绑定                  |
| `channels/`                                                  | 各 IM 渠道配置卡片与路由                                                       | [channels/_ARCH.md](channels/_ARCH.md)        |

## 路由 SSOT（Settings Tab）

`extensionBridge` / `connect` 等 Tab 须在三处登记，缺一即 404、编译报错或被 i18n 校验拦截：

| 登记点                                                                      | 文件                                                                        |
| --------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Tab id `SETTINGS_TABS`（page、Layout、Menu、脏状态 store 共读，缺失即 404） | `settingsRoutes.ts`                                                         |
| 布局 `SECTION_COMPONENTS`（对 `SettingsTab` 穷尽，缺失即编译报错）          | `SettingsLayout.tsx`                                                        |
| 侧栏 `SettingsMenu` + `locales/*` 的 `metadata.settingsTabs` / `settings.menu` | `SettingsMenu.tsx` · `scripts/verify-i18n.mjs`（从 `settingsRoutes.ts` 读取） |

## SettingsMenu 映射（integration 组）

| Tab id                                  | 组件                                        |
| --------------------------------------- | ------------------------------------------- |
| `connect`                               | `ConnectSection`                            |
| `extensionBridge`                       | `ExtensionBridgeSection`                    |
| `integrationCatalog`                    | `integrations/IntegrationCatalogSection`    |
| `integrationMemory`                     | `integrations/IntegrationMemorySection`     |
| `channels` / `channelRouting` / `voice` | `CommunicationSection`（Tauri：`channels`） |
| `openaiApi`                             | `OpenAIApiSection`                          |

`CredentialsSection` 由 `credentials` Tab 路由；`ExternalAgentsConfig` 嵌入 `system/DeveloperSection`。

## 依赖

- `@/hooks/extension/useExtensionWebUiOriginSeed` — App mount 写入 clip `web_ui_origin`
- `@/services/connect` — Connect Wizard REST
- `@/services/extension` — 扩展桥 REST
- `@/services/channels` — 渠道配置
- `../SettingsSection.tsx`
- 父模块 [sections/_ARCH.md](../_ARCH.md)
