//! 把已存在的窗口带到前台。

use tauri::WebviewWindow;

/// 窗口可能是隐藏的，也可能被最小化；只 `set_focus` 对这两种状态是否生效取决于平台，
/// 所以先显式恢复可见与非最小化，再取得焦点。
pub fn reveal_window(window: &WebviewWindow) -> Result<(), String> {
    window.show().map_err(|e| e.to_string())?;
    window.unminimize().map_err(|e| e.to_string())?;
    window.set_focus().map_err(|e| e.to_string())
}
