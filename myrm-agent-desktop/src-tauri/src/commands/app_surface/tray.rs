//! 应用自己的托盘项（macOS 菜单栏项）。
//!
//! [INPUT]
//! - registry::AppSurfaceRegistry (POS: 记录哪些应用有托盘项)
//! - utils::ui_locale (POS: 菜单文案的中英选择)
//! - utils::window_reveal::reveal_window (POS: 把应用窗口带到前台)
//!
//! [OUTPUT]
//! - set_tray: 为调用方窗口的应用建立或更新托盘项（图标是应用窗口图标，tooltip / 状态行来自页面）
//! - clear_tray: 去掉托盘项；窗口此刻藏着就先显示出来，否则它再也没有入口
//! - menu_labels: 菜单三项的文案（按界面语言）
//!
//! [POS]
//! 托盘项在原生菜单里创建，菜单只能在主线程建，所以它的行为在真机二进制上核对，不进 mock 运行时的单元测试。
//! 菜单抬头永远是窗口标题（宿主页以应用名设置，页面改不了），页面给的只是 tooltip 与一行状态文字，
//! 页面因此无法冒充别的程序。菜单有「显示窗口 / 权限… / 退出应用」：关闭窗口只是藏起来（见
//! `app::window_policy`），「退出应用」才销毁窗口并让登记表里它的一切一并清掉。

use tauri::menu::{IsMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, Runtime, WebviewWindow};

use super::registry::AppSurfaceRegistry;
use super::{artifact_id_of, clean_text, window_label};
use crate::utils::ui_locale::ui_locale_is_zh;
use crate::utils::window_reveal::reveal_window;

pub const PERMISSIONS_EVENT: &str = "app-surface:permissions";

const MAX_TITLE_CHARS: usize = 24;
const MAX_TOOLTIP_CHARS: usize = 80;
const FALLBACK_NAME: &str = "MyrmAgent";

pub struct MenuLabels {
    pub show: &'static str,
    pub permissions: &'static str,
    pub quit: &'static str,
}

pub fn menu_labels(zh: bool) -> MenuLabels {
    if zh {
        MenuLabels {
            show: "显示窗口",
            permissions: "权限…",
            quit: "退出应用",
        }
    } else {
        MenuLabels {
            show: "Show window",
            permissions: "Permissions…",
            quit: "Quit app",
        }
    }
}

fn tray_id(artifact_id: &str) -> String {
    format!("app-tray-{artifact_id}")
}

fn build_menu<R: Runtime>(app: &AppHandle<R>, name: &str, status: &str) -> tauri::Result<Menu<R>> {
    let labels = menu_labels(ui_locale_is_zh());
    let header = MenuItem::with_id(app, "name", name, false, None::<&str>)?;
    let status_line = MenuItem::with_id(app, "status", status, false, None::<&str>)?;
    let sep_top = PredefinedMenuItem::separator(app)?;
    let show = MenuItem::with_id(app, "show", labels.show, true, None::<&str>)?;
    let permissions =
        MenuItem::with_id(app, "permissions", labels.permissions, true, None::<&str>)?;
    let sep_bottom = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", labels.quit, true, None::<&str>)?;

    let mut items: Vec<&dyn IsMenuItem<R>> = vec![&header];
    if !status.is_empty() {
        items.push(&status_line);
    }
    items.extend([
        &sep_top as &dyn IsMenuItem<R>,
        &show,
        &permissions,
        &sep_bottom,
        &quit,
    ]);
    Menu::with_items(app, &items)
}

fn on_menu_event<R: Runtime>(app: &AppHandle<R>, artifact_id: &str, item: &str) {
    let label = window_label(artifact_id);
    let Some(window) = app.get_webview_window(&label) else {
        return;
    };
    match item {
        "show" => {
            let _ = reveal_window(&window);
        }
        "permissions" => {
            let _ = reveal_window(&window);
            let _ = app.emit_to(&label, PERMISSIONS_EVENT, ());
        }
        // 销毁而不是关闭：关闭会被「有托盘项的应用只藏起来」拦下。销毁事件触发清理。
        "quit" => {
            let _ = window.destroy();
        }
        _ => {}
    }
}

pub fn set_tray<R: Runtime>(
    app: &AppHandle<R>,
    registry: &AppSurfaceRegistry,
    window: &WebviewWindow<R>,
    title: &str,
    tooltip: &str,
) -> Result<(), String> {
    let artifact_id = artifact_id_of(window.label())
        .ok_or_else(|| "Only an app window can set a tray item".to_string())?
        .to_string();
    let name = clean_text(&window.title().unwrap_or_default(), MAX_TOOLTIP_CHARS);
    let name = if name.is_empty() {
        FALLBACK_NAME.to_string()
    } else {
        name
    };
    let status = clean_text(title, MAX_TITLE_CHARS);
    let tooltip = clean_text(tooltip, MAX_TOOLTIP_CHARS);
    let tooltip = if tooltip.is_empty() {
        name.clone()
    } else {
        tooltip
    };

    let menu = build_menu(app, &name, &status).map_err(|e| e.to_string())?;
    let id = tray_id(&artifact_id);

    if let Some(tray) = app.tray_by_id(&id) {
        tray.set_menu(Some(menu)).map_err(|e| e.to_string())?;
        tray.set_tooltip(Some(&tooltip))
            .map_err(|e| e.to_string())?;
        tray.set_title(Some(&status)).map_err(|e| e.to_string())?;
        return Ok(());
    }

    let icon = app
        .default_window_icon()
        .cloned()
        .ok_or_else(|| "The app has no icon to show in the tray".to_string())?;
    let owner = artifact_id.clone();
    TrayIconBuilder::with_id(id)
        .icon(icon)
        .tooltip(&tooltip)
        .title(&status)
        .menu(&menu)
        .on_menu_event(move |app, event| on_menu_event(app, &owner, event.id().as_ref()))
        .build(app)
        .map_err(|e| e.to_string())?;
    registry.mark_tray(&artifact_id);
    Ok(())
}

pub fn clear_tray<R: Runtime>(
    app: &AppHandle<R>,
    registry: &AppSurfaceRegistry,
    artifact_id: &str,
) {
    app.remove_tray_by_id(&tray_id(artifact_id));
    registry.unmark_tray(artifact_id);
    if let Some(window) = app.get_webview_window(&window_label(artifact_id)) {
        // 没有托盘项后窗口不能继续藏着，否则它没有任何入口。
        if !window.is_visible().unwrap_or(true) {
            let _ = reveal_window(&window);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::menu_labels;

    #[test]
    fn the_menu_speaks_the_ui_language() {
        assert_eq!(menu_labels(true).quit, "退出应用");
        assert_eq!(menu_labels(false).quit, "Quit app");
    }
}
