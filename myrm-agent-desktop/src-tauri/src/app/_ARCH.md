# app 模块架构

[INPUT]
- commands / runtime / config / utils 各模块
- Tauri Builder 插件注册表

[OUTPUT]
- run(): 应用主循环、IPC handler、托盘、优雅停机
- chain: 产品插件链与页面协议的共享装配（生产与测试同源）

[POS]
Tauri 应用组装层；main.rs 唯一委托目标。

## 架构概述

Tauri 应用组装层：插件注册、全局快捷键分发、`setup` 钩子、窗口事件、IPC handler 清单、统一 IPC sender gate、托盘与优雅停机。

父模块：[../_ARCH.md](../_ARCH.md)

## 文件清单

| 文件 | 职责 |
|------|------|
| `mod.rs` | `run()`：Builder 链（单实例 / 窗口状态 → `chain` 的产品插件与页面协议 → 全局快捷键）、基于 `command_registry_macro.in` 的 `generate_handler`、退出优雅停机 |
| `chain.rs` | 产品插件链（`with_product_plugins`）与页面自定义协议（`with_page_protocols`：帷幕看板 `myrm-curtain`、审批高亮 `myrm-overlay`）的共享装配，对 `Runtime` 泛型：`run()` 与进程内测试走同一条链。单实例、窗口状态、全局快捷键依赖真实桌面会话，留在 `run()`。测试用真实 `generate_context!()` 起一个 `MockRuntime` 应用，证明发布配置被每个插件接受（插件配置多为 `deny_unknown_fields`，错键即启动 panic——并有一条故意放入错键的反例证明该测试会失败），并校验 `plugins.shell.open` 链接白名单 |
| `autostart.rs` | 系统登录项（开机自启）的唯一写入口 `sync_login_item`，启动、保存设置、重置设置三处共用。开发构建（`debug_assertions`）一律不读不写：登录项登记的是当前运行的二进制路径、且只在条目缺失时才写，开发构建抢先登记会让之后安装的正式版也改不回来；发布构建只在条目与开关不一致时才写。策略与系统调用之间隔一个 `LoginItem` trait，测试用记录调用的替身覆盖开发构建零调用、发布构建按开关启停、已一致不重写、读取失败不动、写入被拒只尝试一次，不触碰真实系统；另用产品插件链起一个 `MockRuntime` 应用，证明 `sync_login_item` 与产品注册的 autostart 插件状态接得上（请求值取自当前状态，任何构建下都不会改动测试机器的真实登录项） |
| `setup.rs` | `on_setup` / `on_window_event`：配置、产品页来源 `ProductOrigin` 与 ACL 能力注册（配置载入后、任何产品页发起 IPC 之前）、Sidecar 自启（Next 始终；Python 在 Remote 跟随态 defer）、托盘、隐私帷幕 watcher spawn；启动失败 emit `backend-start-failed` / `frontend-start-failed`；后端启动成功后经 `runtime::spawn_backend_monitors` 统一挂载 watchdog+wake。`on_window_event` 把「关闭到托盘」「有托盘项的应用窗口只藏起来」「应用窗口还开着时的退出确认」与「销毁即优雅停机」交给 `window_policy` 判定；应用窗口销毁时归还它在托盘与全局快捷键上登记的内容 |
| `quit_gate.rs` | 退出确认：托盘「退出」、macOS 菜单「Quit」/Cmd+Q（`menu.rs` 的自定义退出项，系统预置的退出项直接 `terminate:`、进程不经确认就没了）与主窗口在「关闭到托盘」关着时的关闭都经 `request_quit`；没有应用窗口就直接优雅停机，有就弹一次确认（文案含应用窗口数，含藏在托盘里的），确认后才退出；对话框文案随进程环境选中英；应用显式 `app.exit(code)`、Dock 菜单「Quit」与系统关机登出不经确认（后两者走 `NSApp terminate:`，应用拦不下） |
| `window_policy.rs` | 应用级窗口策略的纯决策：「关闭到托盘」与「销毁即停机」只管辖主窗口，设置文案只承诺主窗口。会话窗、宠物层、应用窗、帷幕与审批高亮被关闭就是被关闭——被拦成隐藏窗会把 macOS 切成 Accessory（主窗口仍开着，Dock 图标却消失），销毁被当作应用退出会把前后端一并停掉。唯一例外是有托盘项的应用窗口（用户授权了它常驻）：关闭只是藏起来，不碰激活策略；主窗口在「关闭到托盘」关着时的关闭就是退出主进程，应用窗口会一并消失，所以此时先走退出确认 |
| `shortcut_handler.rs` | 全局快捷键事件分发：先看是不是某个已安装应用占用的组合（是就把事件发给该应用的窗口），再走 Myrm 自身的四个入口 |
| `linux_gpu.rs` | Linux NVIDIA + Wayland WebKitGTK 兼容 |
| `menu.rs` | 原生应用主菜单与 Edit 快捷键桥接（macOS AppKit / Win32 Responder Chain）；macOS 的「Quit」是自定义项（Cmd+Q），点击经 `quit_gate`，不用系统预置退出项 |
| `tray.rs` | 系统托盘菜单（含「Apps」项：显示主窗口并发 `tray:apps`，前端路由到 `/artifacts` 的应用列表；「退出」经 `quit_gate`）、状态 tooltip、隐私帷幕切换项、IPC `set_tray_status` / `update_tray_info`。托盘图标是模板图标（macOS 随菜单栏深浅自动着色，只按透明度绘制、不显示颜色），换图一律经 `apply_tray_icon`：托盘库的 `set_icon` 会重置模板标志，不恢复则图标按纯黑像素显示、在深色菜单栏上看不清，所以经 `with_inner_tray_icon` 由 Tauri 在主线程调用 `set_icon_with_as_template` 一次完成换图与模板标志；该函数在非 macOS 上什么都不做，其他平台改用 `set_icon`。三个状态图靠轮廓区分（空闲、忙的圆点、异常的三角），图标目录里的每个文件都被本文件嵌入（`test_desktop_tray_icon.py` 钉住） |
| `lifecycle.rs` | 优雅停机：`SHUTDOWN_INITIATED` 防重入 + `graceful_stop_backend`（停 monitors → 服务端 drain → 等待自退 → 兜底强杀）+ Sidecar 有序关闭；后端停止后代解锁租约仍未交还则 `relock_outstanding_lease` 兜底回锁（帷幕窗口随进程消失，不能留下解锁态桌面） |

## 依赖

- `runtime` / `commands` / `config` / `utils`
