# app_launcher 子模块架构

[INPUT]
- `plan`（各平台启动器的文件与标记，纯数据）
- `store`（在给定目录下写入、查找、移除启动器）
- Tauri path resolver（用户主目录与数据目录，决定启动器放哪）

[OUTPUT]
- IPC: `has_app_launcher` / `create_app_launcher` / `remove_app_launcher`（按产物 ID 管理一个启动器）

[POS]
已安装应用的系统入口：Spotlight / 开始菜单 / 应用菜单里的启动器。启动器只打开 `myrmagent://app/<id>`，由主应用的深链接分发开应用窗口。

## 架构概述

启动器本身不含逻辑，只负责打开 `myrmagent://app/<id>`；主应用没在运行时，系统会先把它拉起。入口只放在用户自己的目录里，不写系统目录、不需要提权：macOS 在 `~/Applications/Myrm Apps/` 生成 `.app`，Windows 建开始菜单快捷方式，Linux 写 `.desktop`。本地生成、不带隔离标记，所以 macOS 不经公证也能打开。Windows 与 Linux 的实际效果未验证。

「这个入口是哪个应用的」写在启动器内容里的标记行，不靠文件名：同名应用并存时文件名会带短 ID，改名后要能找到并替换旧入口。`store` 只碰标记匹配的入口，用户放在同目录的别的东西不读不改不删；写入先落在目录内的临时入口再改名，系统索引不会看到写了一半的启动器。

主窗口与应用窗口可调用（宿主页的菜单在两处都有），会话窗口与宠物层不能调；应用窗口只能管理自己的启动器（标签推出的 ID 必须与参数相同）。

父模块：[../_ARCH.md](../_ARCH.md)

## 文件清单

| 文件 | 职责 | I/O/P |
|------|------|-------|
| `mod.rs` | 三条 IPC 命令、调用方校验（ID 合法、应用窗口只能操作自己的）、各平台启动器目录 `launcher_dir` | ✅ |
| `plan.rs` | 各平台要写的文件与标记，纯数据、不碰磁盘：`Platform`、`Plan`、`build_plan`、`launcher_url`；应用名进 plist / `.desktop` / 文件名前的清理（控制字符去掉、XML 转义、路径字符与 Windows 保留名处理）；整行标记判定 `contains_marker` | ✅ |
| `store.rs` | 在给定目录下 `install`（先清掉这个应用已有的入口再写新的）、`find`、`remove`；只碰标记匹配的入口 | ✅ |
