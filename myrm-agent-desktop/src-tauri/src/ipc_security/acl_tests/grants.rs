//! 各窗口的授予表契约：帷幕页、产品页的最小授权，以及外部来源什么都拿不到。

use super::*;

/// 帷幕页是唯一非受控前端：只能上报物理输入，内核与插件命令一概不给。
#[test]
fn curtain_page_can_only_report_physical_input() {
    let app = product_app();
    let pages = [curtain_page::url().expect("curtain url").to_string()];
    let everything_else: Vec<&str> = REGISTERED_COMMANDS
        .iter()
        .copied()
        .filter(|command| *command != "curtain_report_physical_input")
        .chain(PRODUCT_PAGE_GRANTED.iter().copied())
        .chain(PRODUCT_PAGE_WITHHELD.iter().copied())
        .collect();
    assert_grants(
        &app,
        "privacy-curtain-1-0",
        &pages,
        &["curtain_report_physical_input"],
        &everything_else,
    );
}

/// 内核与官方插件命令按前端实际用法授予：授予表之外（含高危动词）一律不给，
/// 且主窗口与会话窗口、Local 与 Remote 来源得到同一张表。
#[test]
fn native_grants_follow_the_least_privilege_table() {
    let app = product_app();
    let pages = product_pages();

    for label in ["main", "session-chat-1"] {
        assert_grants(
            &app,
            label,
            &pages,
            PRODUCT_PAGE_GRANTED,
            PRODUCT_PAGE_WITHHELD,
        );
    }

    let app_withheld: Vec<&str> = PRODUCT_PAGE_GRANTED
        .iter()
        .chain(PRODUCT_PAGE_WITHHELD)
        .copied()
        .filter(|command| !APP_WINDOW_GRANTED.contains(command))
        .collect();
    assert_grants(&app, "app-art-1", &pages, APP_WINDOW_GRANTED, &app_withheld);

    let pet_withheld: Vec<&str> = PRODUCT_PAGE_GRANTED
        .iter()
        .chain(PRODUCT_PAGE_WITHHELD)
        .copied()
        .filter(|command| !PET_SURFACE_GRANTED.contains(command))
        .collect();
    assert_grants(
        &app,
        "pet-surface",
        &pages,
        PET_SURFACE_GRANTED,
        &pet_withheld,
    );
}

/// 产品页 capability 只绑定配置端口的回环源：其它端口、端口前缀、`localhost`、其它协议与外部站点
/// 即使落在主窗口里也什么都拿不到。
#[test]
fn foreign_origins_get_nothing_even_in_the_main_window() {
    let app = product_app();
    let main = window(&app, "main");
    let foreign_pages = [
        "http://127.0.0.1:3778/",
        "http://127.0.0.1:37770/",
        "http://localhost:3777/",
        "https://127.0.0.1:3777/",
        "http://127.0.0.1.evil.example:3777/",
        "https://evil.example/",
    ];
    for page in foreign_pages {
        for command in REGISTERED_COMMANDS
            .iter()
            .chain(PRODUCT_PAGE_GRANTED)
            .chain(APP_WINDOW_GRANTED)
            .chain(PET_SURFACE_GRANTED)
        {
            assert!(
                !passes_acl(&main, page, command),
                "{page} must not reach {command}"
            );
        }
    }
}
