//! Installed-app window — an installed HTML app opens in its own standalone window.
//!
//! [INPUT]
//! - runtime::ProductOrigin (POS: 产品页来源，决定窗口加载哪个地址)
//! - utils::window_reveal::reveal_window (POS: 已存在的窗口带到前台)
//! - ipc_security::APP_WEBVIEW_PREFIX (POS: 应用窗口标签前缀，与 IPC gate / ACL 同源)
//!
//! [OUTPUT]
//! - open_app_window: 打开（或带到前台）`app-<artifact_id>` 窗口，加载产品页 `/apps/<artifact_id>?mode=focused`
//! - close_app_window: 关闭调用它的应用窗口
//! - set_app_window_always_on_top: 切换调用它的应用窗口是否置顶
//!
//! [POS]
//! 应用窗口是主进程里的从属窗口（与会话窗同类）：没有自己的 Dock 图标与进程，被关闭就是被关闭
//! （见 `app::window_policy`）。后两个命令作用于「调用方窗口」，所以只允许应用窗口自己调用，
//! 由 IPC gate 与此处的标签前缀校验共同保证，主窗口无法借它们关掉或置顶自己。

use tauri::{AppHandle, Manager, State, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

use crate::ipc_security::APP_WEBVIEW_PREFIX;
use crate::runtime::ProductOrigin;
use crate::utils::window_reveal::reveal_window;

const MAX_APP_ID_LEN: usize = 64;
const MAX_TITLE_CHARS: usize = 80;
const FALLBACK_TITLE: &str = "MyrmAgent";

/// 标签只接受产物 ID 的字符集：窗口标签进入 ACL 匹配与窗口状态存储，不能带路径或通配字符。
fn app_window_label(artifact_id: &str) -> Result<String, String> {
    let valid = !artifact_id.is_empty()
        && artifact_id.len() <= MAX_APP_ID_LEN
        && artifact_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_');
    if valid {
        Ok(format!("{APP_WEBVIEW_PREFIX}{artifact_id}"))
    } else {
        Err("Invalid app id".to_string())
    }
}

/// 应用窗口页面地址：产品页来源上的 `/apps/<artifact_id>?mode=focused`（`focused` 即不带侧栏的独立窗口）。
fn app_window_url(origin: &ProductOrigin, artifact_id: &str) -> Url {
    origin.page_url(&["apps", artifact_id], &[("mode", "focused")])
}

/// 窗口标题取应用名；空名与超长名都不应撑坏标题栏。
fn app_window_title(name: &str) -> String {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        FALLBACK_TITLE.to_string()
    } else {
        trimmed.chars().take(MAX_TITLE_CHARS).collect()
    }
}

fn require_app_window(window: &WebviewWindow) -> Result<(), String> {
    if window.label().starts_with(APP_WEBVIEW_PREFIX) {
        Ok(())
    } else {
        Err("Only an app window can change itself through this command".to_string())
    }
}

#[tauri::command]
pub fn open_app_window(
    app: AppHandle,
    origin: State<'_, ProductOrigin>,
    artifact_id: String,
    title: String,
) -> Result<(), String> {
    let label = app_window_label(&artifact_id)?;

    if let Some(existing) = app.get_webview_window(&label) {
        return reveal_window(&existing);
    }

    let url = app_window_url(&origin, &artifact_id);

    WebviewWindowBuilder::new(&app, &label, WebviewUrl::External(url))
        .title(app_window_title(&title))
        .inner_size(960.0, 720.0)
        .min_inner_size(480.0, 360.0)
        .resizable(true)
        .maximizable(true)
        .minimizable(true)
        .closable(true)
        .focused(true)
        .visible(true)
        .build()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn close_app_window(window: WebviewWindow) -> Result<(), String> {
    require_app_window(&window)?;
    window.close().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn set_app_window_always_on_top(
    window: WebviewWindow,
    always_on_top: bool,
) -> Result<(), String> {
    require_app_window(&window)?;
    window
        .set_always_on_top(always_on_top)
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::{app_window_label, app_window_title, app_window_url, MAX_APP_ID_LEN};
    use crate::runtime::ProductOrigin;

    #[test]
    fn app_window_label_carries_the_app_prefix() {
        assert_eq!(app_window_label("art-1_x").as_deref(), Ok("app-art-1_x"));
    }

    #[test]
    fn ids_that_could_widen_the_acl_pattern_or_escape_the_label_are_rejected() {
        let too_long = "a".repeat(MAX_APP_ID_LEN + 1);
        for id in [
            "", "a/b", "a b", "../x", "a*", "a:b", "é", "a\0b", &too_long,
        ] {
            assert!(app_window_label(id).is_err(), "{id:?}");
        }
        assert!(app_window_label(&"a".repeat(MAX_APP_ID_LEN)).is_ok());
    }

    #[test]
    fn app_window_opens_the_focused_app_route() {
        let url = app_window_url(&ProductOrigin::new(3777), "art-1");
        assert_eq!(
            url.as_str(),
            "http://127.0.0.1:3777/apps/art-1?mode=focused"
        );
    }

    #[test]
    fn window_title_is_the_trimmed_app_name_with_a_fallback_and_a_cap() {
        assert_eq!(app_window_title("  Weather  "), "Weather");
        assert_eq!(app_window_title("   "), "MyrmAgent");
        assert_eq!(app_window_title(&"天".repeat(200)).chars().count(), 80);
    }
}
