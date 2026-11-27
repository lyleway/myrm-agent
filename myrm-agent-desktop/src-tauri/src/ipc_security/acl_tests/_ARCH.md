# acl_tests 子模块架构

[INPUT]
- `../acl_tests.rs` 的 mock 应用与断言（`product_app`、`window`、`assert_grants`、`passes_acl`）和授予表常量（`PRODUCT_PAGE_GRANTED`、`PRODUCT_PAGE_WITHHELD`、`APP_WINDOW_GRANTED`、`PET_SURFACE_GRANTED`）
- `../capability.rs`（`REGISTERED_COMMANDS`、`register_product_capabilities`）

[OUTPUT]
- 各窗口授予表的契约测试：帷幕页、产品页、应用窗口、宠物层各自拿到什么，异源页面拿到什么

[POS]
进程内 ACL 契约测试里「授予表」一族的用例；共用的 mock 应用与断言留在父文件 `../acl_tests.rs`。

## 架构概述

用真实 `generate_context!()` 与生产使用的 `register_product_capabilities`，在 `MockRuntime` 上逐条核对「窗口标签 × 页面来源 × 命令」，钉住授予表本身：

- 帷幕页是唯一非受控前端，只能上报物理输入，内核与插件命令一概不给。
- 内核与官方插件命令按前端实际用法最小授予：授予表之外（含高危动词）一律不给，主窗口与会话窗口、Local 与 Remote 来源得到同一张表；应用窗口与宠物层各有更小的表。
- 产品页 capability 只绑定配置端口的回环源：其它端口、端口前缀、`localhost`、其它协议与外部站点即使落在主窗口里也什么都拿不到。

父模块：[../_ARCH.md](../_ARCH.md)

## 文件清单

| 文件 | 职责 | I/O/P |
|------|------|-------|
| `grants.rs` | 三条授予表契约：`curtain_page_can_only_report_physical_input`、`native_grants_follow_the_least_privilege_table`、`foreign_origins_get_nothing_even_in_the_main_window` | — |
