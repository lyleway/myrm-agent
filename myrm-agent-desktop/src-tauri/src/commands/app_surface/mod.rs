//! 应用在桌面上的东西：托盘项、全局快捷键、窗口标题。
//!
//! [INPUT]
//! - registry::AppSurfaceRegistry (POS: Tauri 托管状态，按应用归属的登记表)
//! - ipc_security::APP_WEBVIEW_PREFIX (POS: 应用窗口标签前缀，与 IPC gate / ACL 同源)
//!
//! [OUTPUT]
//! - set_app_tray / clear_app_tray / register_app_shortcut / unregister_app_shortcut / set_app_window_title: IPC 命令
//! - on_app_window_destroyed: 应用窗口真正销毁时归还它占的一切（托盘项、全部组合）
//! - app_has_tray / app_resident_count: 窗口策略与退出确认需要的只读查询
//! - artifact_id_of / window_label: 窗口标签与产物 ID 的互转
//!
//! [POS]
//! 五个命令都作用于「调用方窗口的应用」：应用由窗口标签推出，页面没有地方可以填别的应用的 ID，
//! 所以它碰不到别人的托盘项与组合；它们只允许应用窗口调用（IPC gate 的 `AppOnly`，加这里的标签校验）。
//! 授权与否由宿主页（前端桥）在调用前把关，这里只管「做得到、不越界」：组合写法、占用冲突、数量上限、
//! 文字长度。命令是 `Wry` 上的薄封装，逻辑在对运行时泛型或对插件参数化的函数里，测试直接驱动。

mod accelerator;
mod registry;
pub mod shortcuts;
mod tray;

use tauri::{AppHandle, Manager, Runtime, State, WebviewWindow};

use crate::ipc_security::APP_WEBVIEW_PREFIX;
pub use registry::AppSurfaceRegistry;

const MAX_TITLE_CHARS: usize = 80;

pub fn artifact_id_of(label: &str) -> Option<&str> {
    label
        .strip_prefix(APP_WEBVIEW_PREFIX)
        .filter(|id| !id.is_empty())
}

pub fn window_label(artifact_id: &str) -> String {
    format!("{APP_WEBVIEW_PREFIX}{artifact_id}")
}

/// 页面给的文字进入原生界面（窗口标题、托盘菜单）：去掉控制字符，按字符数截断，去掉首尾空白。
pub(super) fn clean_text(text: &str, max_chars: usize) -> String {
    text.chars()
        .filter(|c| !c.is_control())
        .take(max_chars)
        .collect::<String>()
        .trim()
        .to_string()
}

fn calling_app<R: Runtime>(window: &WebviewWindow<R>) -> Result<&str, String> {
    artifact_id_of(window.label())
        .ok_or_else(|| "Only an app window can use this command".to_string())
}

/// 窗口关闭请求时的查询：该应用有托盘项就只藏起来。
pub fn app_has_tray(registry: &AppSurfaceRegistry, label: &str) -> bool {
    artifact_id_of(label).is_some_and(|id| registry.has_tray(id))
}

pub fn on_app_window_destroyed(app: &AppHandle, label: &str) {
    let Some(artifact_id) = artifact_id_of(label) else {
        return;
    };
    let registry = app.state::<AppSurfaceRegistry>();
    tray::clear_tray(app, &registry, artifact_id);
    shortcuts::unregister_all(app, &registry, artifact_id);
}

#[tauri::command]
pub fn set_app_tray(
    app: AppHandle,
    window: WebviewWindow,
    registry: State<'_, AppSurfaceRegistry>,
    title: String,
    tooltip: String,
) -> Result<(), String> {
    calling_app(&window)?;
    tray::set_tray(&app, &registry, &window, &title, &tooltip)
}

#[tauri::command]
pub fn clear_app_tray(
    app: AppHandle,
    window: WebviewWindow,
    registry: State<'_, AppSurfaceRegistry>,
) -> Result<(), String> {
    let artifact_id = calling_app(&window)?;
    tray::clear_tray(&app, &registry, artifact_id);
    Ok(())
}

#[tauri::command]
pub fn register_app_shortcut(
    app: AppHandle,
    window: WebviewWindow,
    registry: State<'_, AppSurfaceRegistry>,
    accelerator: String,
) -> Result<(), String> {
    let artifact_id = calling_app(&window)?;
    shortcuts::register(&app, &registry, artifact_id, &accelerator)
}

/// `accelerator` 为空表示注销本应用的全部组合。
#[tauri::command]
pub fn unregister_app_shortcut(
    app: AppHandle,
    window: WebviewWindow,
    registry: State<'_, AppSurfaceRegistry>,
    accelerator: Option<String>,
) -> Result<(), String> {
    let artifact_id = calling_app(&window)?;
    shortcuts::unregister(&app, &registry, artifact_id, accelerator.as_deref())
}

fn retitle<R: Runtime>(window: &WebviewWindow<R>, title: &str) -> Result<(), String> {
    calling_app(window)?;
    window
        .set_title(&clean_text(title, MAX_TITLE_CHARS))
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn set_app_window_title(window: WebviewWindow, title: String) -> Result<(), String> {
    retitle(&window, &title)
}

#[cfg(test)]
mod tests {
    use tauri::test::{mock_builder, mock_context, noop_assets, MockRuntime};
    use tauri::{App, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

    use super::{
        app_has_tray, artifact_id_of, calling_app, clean_text, retitle, window_label,
        AppSurfaceRegistry,
    };

    fn mock_app() -> App<MockRuntime> {
        mock_builder()
            .build(mock_context(noop_assets()))
            .expect("the mock app builds")
    }

    fn mock_window(app: &App<MockRuntime>, label: &str) -> WebviewWindow<MockRuntime> {
        WebviewWindowBuilder::new(app, label, WebviewUrl::default())
            .build()
            .expect("the mock window builds")
    }

    #[test]
    fn only_an_app_window_may_use_the_surface_commands() {
        let app = mock_app();
        let app_window = mock_window(&app, "app-art-1");

        assert_eq!(calling_app(&app_window), Ok("art-1"));
        for label in ["main", "session-42", "pet-surface"] {
            let other = mock_window(&app, label);
            assert!(calling_app(&other).is_err(), "{label}");
        }
    }

    #[test]
    fn only_an_app_window_may_retitle_itself() {
        let app = mock_app();
        let app_window = mock_window(&app, "app-art-1");
        let main = mock_window(&app, "main");

        assert_eq!(retitle(&app_window, "Weather"), Ok(()));
        assert!(retitle(&main, "x").is_err());
    }

    #[test]
    fn page_text_loses_control_characters_and_is_cut_to_length() {
        assert_eq!(clean_text("  3 unread\n", 24), "3 unread");
        assert_eq!(clean_text("a\u{0}b\tc\u{7}d", 24), "abcd");
        assert_eq!(clean_text("一二三四五六", 3), "一二三");
        assert_eq!(clean_text("\n\n", 24), "");
    }

    #[test]
    fn the_app_is_the_part_of_the_label_after_the_prefix() {
        assert_eq!(artifact_id_of("app-art-1"), Some("art-1"));
        assert_eq!(artifact_id_of("main"), None);
        assert_eq!(artifact_id_of("session-3"), None);
        assert_eq!(artifact_id_of("app-"), None);
        assert_eq!(window_label("art-1"), "app-art-1");
    }

    #[test]
    fn only_an_app_with_a_tray_item_is_hidden_instead_of_closed() {
        let registry = AppSurfaceRegistry::default();
        registry.mark_tray("art-1");

        assert!(app_has_tray(&registry, "app-art-1"));
        assert!(!app_has_tray(&registry, "app-art-2"));
        assert!(!app_has_tray(&registry, "main"));
    }
}
