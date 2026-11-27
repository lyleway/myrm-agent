# app_surface 子模块架构

[INPUT]
- `registry::AppSurfaceRegistry`（Tauri 托管状态，按应用归属的登记表）
- `ipc_security::APP_WEBVIEW_PREFIX`（应用窗口标签前缀，与 IPC gate / ACL 同源）
- `tauri_plugin_global_shortcut`（操作系统级快捷键注册，与 Myrm 自身四个全局入口共用一个插件）

[OUTPUT]
- IPC: `set_app_tray` / `clear_app_tray` / `register_app_shortcut` / `unregister_app_shortcut` / `set_app_window_title`
- `on_app_window_destroyed`：应用窗口真正销毁时归还它占的一切（托盘项、全部组合）
- `app_has_tray` / `app_resident_count`：窗口策略与退出确认需要的只读查询
- `artifact_id_of` / `window_label`：窗口标签与产物 ID 的互转

[POS]
应用在桌面上的东西：托盘项、全局快捷键、窗口标题。

## 架构概述

五个命令都作用于「调用方窗口的应用」：应用由窗口标签推出，页面没有地方可以填别的应用的 ID，所以它碰不到别人的托盘项与组合。它们只允许应用窗口调用（IPC gate 的 `AppOnly`，加这里的标签校验）。授权与否由宿主页（前端桥）在调用前把关，这里只管「做得到、不越界」：组合写法、占用冲突、数量上限、文字长度。

命令是 `Wry` 上的薄封装，逻辑在对运行时泛型或对插件参数化的函数里，测试直接驱动。托盘项在原生菜单里创建，菜单只能在主线程建，所以它的行为在真机二进制上核对，不进 mock 运行时的单元测试。

组合键冲突一律拒绝而不覆盖：已被别的应用、Myrm 自身入口或系统占用时，页面得到 `conflict`；每个应用至多 4 个（与服务端声明的上限同值，这里是第二道闸）。Myrm 自身入口被用户改成某个应用已占的组合时，`update_global_shortcut` 的整体重注册会让应用丢掉该组合，`restore` 把能恢复的恢复回来，恢复不了的从登记表移除。

父模块：[../_ARCH.md](../_ARCH.md)

## 文件清单

| 文件 | 职责 | I/O/P |
|------|------|-------|
| `mod.rs` | 五条 IPC 命令的薄封装、调用方校验、`on_app_window_destroyed`、只读查询、标签与产物 ID 互转 | ✅ |
| `registry.rs` | 纯数据的登记表（Tauri 托管状态）：哪些应用有托盘项、哪个应用占了哪个全局快捷键（`Claim`：新占用 / 已是本应用的 / 被他人占用），每应用上限 `MAX_SHORTCUTS_PER_APP` | ✅ |
| `accelerator.rs` | 组合键写法的规范化与解析，与服务端 `capabilities.normalize_accelerator`、前端 `lib/app-bridge/accelerator.ts` 是同一条规则 | ✅ |
| `shortcuts.rs` | 注册、注销、触发分发（事件 `app-surface:shortcut` 按窗口标签发给所属应用）、全局重置后的恢复；核心流程对「向插件注册、注销」参数化 | ✅ |
| `tray.rs` | 应用自己的托盘项（macOS 菜单栏项）：菜单为「显示窗口 / 权限… / 退出应用」，窗口藏着时撤托盘项会先把窗口显示出来 | ✅ |
