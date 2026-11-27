# desktop/

## 架构概述

本地/Tauri 桌面自动化权限引导的纯函数 SSOT：从 probe `settings_deeplinks` 选取 URL，并通过 Tauri shell 或 Web fallback 打开系统设置；另含已安装应用的桌面窗口控制、托盘 / 全局组合键 / 置顶的壳端调用与系统启动器。

## 文件清单

| 文件                                   | 地位 | 职责                                                                                                                                          | I/O/P |
| -------------------------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| `permissionDeepLink.ts`                | 核心 | `pickSettingsDeepLink*`；`openPermissionDeepLink`；`openPermissionDeepLinkWithGuideFallback(url, platform?)`；`getPermissionGuideFallbackUrl` | ✅    |
| `desktopPermissionsStatus.ts`          | 核心 | `DesktopPermissionsStatus` + `desktopPermissionsPath` — FE contract for `GET /webui/desktop/permissions`                                      | ✅    |
| `desktopLockedUseDoctor.ts`            | 核心 | DesktopLockedUse doctor 解析：badge/detail/tone + `locked_use_enabled` 协调态                                      | ✅    |
| `desktopSettingsAnchors.ts`            | 核心 | 系统设置内离席/帷幕卡片 scroll 锚点 SSOT                                                                           | ✅    |
| `appWindow.ts`                         | 核心 | 已安装应用的桌面窗口控制：`openAppWindow` / `closeAppWindow` / `setAppWindowAlwaysOnTop` / `setAppWindowTitle` 各以布尔报告成败、不抛出，调用方失败时回落到页内路由 `/apps/[id]` | ✅    |
| `appSurface.ts`                        | 核心 | 应用窗口的托盘项、全局组合键、置顶与壳事件：命令由壳按调用窗口的标签确定归属，页面碰不到别的应用的托盘项与组合键；被占用的组合键与失败分开报告（`conflict` / `failed`）；监听用窗口自己的监听器，因为壳按标签发的事件只有它收得到 | ✅    |
| `appLauncher.ts`                       | 核心 | 应用的系统启动器（Spotlight / 开始菜单 / 应用菜单）：读取、创建、删除，启动器只打开 `myrmagent://app/<id>`；各以布尔报告成败、不抛出 | ✅    |
| `__tests__/permissionDeepLink.test.ts` | 测试 | pick meta / system URL / Tauri open / platform guide fallback（含 darwin≠win32 回归）                                                         | —     |
| `__tests__/appWindow.test.ts`          | 测试 | 四个命令的参数与成败报告                                                                                                                             | —     |
| `__tests__/appSurface.test.ts`         | 测试 | 各调用的命令与参数、组合键冲突与失败的区分、事件经窗口监听器收到、不抛出                                                                             | —     |
| `__tests__/appLauncher.test.ts`        | 测试 | 各命令带上应用 ID、被拒绝时以值报告                                                                                                                  | —     |
| `__tests__/desktopLockedUseDoctor.test.ts` | 测试 | DesktopLockedUse doctor 解析与 i18n detail key 映射                                                                                           | —     |

## 依赖

- `@tauri-apps/plugin-shell` — Tauri 桌面打开 `x-apple.systempreferences:` / `ms-settings:`
- 消费者：`DoctorDashboard`、`CuPermissionInline`、`DesktopPermissionsCard`、`DesktopLiveView`（权限 DTO 经 `desktopPermissionsStatus.ts`）
