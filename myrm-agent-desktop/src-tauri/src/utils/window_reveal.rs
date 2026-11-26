//! 把已存在的窗口带到前台。
//!
//! [INPUT]
//! - tauri::WebviewWindow (POS: 要带到前台的已存在窗口，由调用方按标签取得)
//!
//! [OUTPUT]
//! - reveal_window: 先显式恢复可见与非最小化，再取得焦点；任一步失败即返回错误文本
//!
//! [POS]
//! 复用已存在窗口的共用原语：会话窗（`commands::session_window`）与应用窗（`commands::app_window`）
//! 在窗口已存在时共用，`show` / `unminimize` / `set_focus` 的顺序只在这里定义一次。

use tauri::WebviewWindow;

/// 窗口可能是隐藏的，也可能被最小化；只 `set_focus` 对这两种状态是否生效取决于平台，
/// 所以先显式恢复可见与非最小化，再取得焦点。
pub fn reveal_window(window: &WebviewWindow) -> Result<(), String> {
    window.show().map_err(|e| e.to_string())?;
    window.unminimize().map_err(|e| e.to_string())?;
    window.set_focus().map_err(|e| e.to_string())
}
