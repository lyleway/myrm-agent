//! 退出确认：应用窗口还开着时，退出前先让用户确认它们会一并关闭。
//!
//! [INPUT]
//! - commands::app_surface::artifact_id_of (POS: 窗口标签是否属于已安装应用)
//! - utils::ui_locale (POS: 对话框文案的中英选择)
//! - lifecycle::shutdown_and_exit (POS: 确认后的优雅停机)
//!
//! [OUTPUT]
//! - app_window_count: 当前应用窗口数（含藏在托盘里的）
//! - request_quit: 没有应用窗口就直接退出；有就弹一次确认，确认后才退出
//! - quit_copy: 确认框文案（含应用窗口数）
//!
//! [POS]
//! 应用窗口是主进程里的窗口：主进程退出，它们与它们的托盘项、全局快捷键都消失；藏在托盘里的应用
//! 用户看不见，退出前必须让用户知道（托盘「Quit」、macOS 菜单「Quit」/Cmd+Q、关闭主窗口且未开「关闭到托盘」三条路都经这里）。
//! 不经这里的退出：Dock 菜单「Quit」与系统关机/登出（它们直接走 `NSApp terminate:`，应用拦不下），应用显式 `app.exit(code)`。
//! 同一时刻只弹一个框：确认框开着时再次请求退出被忽略，不叠框。

use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{AppHandle, Manager, Runtime};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

use super::lifecycle;
use crate::commands::app_surface::artifact_id_of;
use crate::utils::ui_locale::ui_locale_is_zh;

static CONFIRM_OPEN: AtomicBool = AtomicBool::new(false);

pub struct QuitCopy {
    pub title: &'static str,
    pub message: String,
    pub quit: &'static str,
    pub cancel: &'static str,
}

pub fn quit_copy(zh: bool, app_windows: usize) -> QuitCopy {
    if zh {
        QuitCopy {
            title: "退出 MyrmAgent",
            message: format!(
                "退出后，{app_windows} 个应用窗口将关闭，它们的托盘项与快捷键也会消失。"
            ),
            quit: "退出",
            cancel: "取消",
        }
    } else {
        let (count, noun) = if app_windows == 1 {
            ("1".to_string(), "app window")
        } else {
            (app_windows.to_string(), "app windows")
        };
        QuitCopy {
            title: "Quit MyrmAgent",
            message: format!(
                "Quitting closes {count} {noun}, along with their tray items and shortcuts."
            ),
            quit: "Quit",
            cancel: "Cancel",
        }
    }
}

pub fn app_window_count<R: Runtime>(app: &AppHandle<R>) -> usize {
    app.webview_windows()
        .keys()
        .filter(|label| artifact_id_of(label).is_some())
        .count()
}

fn quit_now(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        lifecycle::shutdown_and_exit(app, 0).await;
    });
}

pub fn request_quit(app: AppHandle) {
    let app_windows = app_window_count(&app);
    if app_windows == 0 {
        quit_now(app);
        return;
    }
    if CONFIRM_OPEN.swap(true, Ordering::SeqCst) {
        return;
    }
    let copy = quit_copy(ui_locale_is_zh(), app_windows);
    let app_for_confirm = app.clone();
    app.dialog()
        .message(copy.message)
        .title(copy.title)
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            copy.quit.to_string(),
            copy.cancel.to_string(),
        ))
        .show(move |confirmed| {
            CONFIRM_OPEN.store(false, Ordering::SeqCst);
            if confirmed {
                quit_now(app_for_confirm);
            }
        });
}

#[cfg(test)]
mod tests {
    use tauri::test::{mock_builder, mock_context, noop_assets};
    use tauri::{WebviewUrl, WebviewWindowBuilder};

    use super::{app_window_count, quit_copy};

    #[test]
    fn only_app_windows_count_toward_the_quit_confirmation() {
        let app = mock_builder()
            .build(mock_context(noop_assets()))
            .expect("the mock app builds");
        for label in ["main", "session-1", "app-a", "app-b", "pet-surface"] {
            WebviewWindowBuilder::new(&app, label, WebviewUrl::default())
                .build()
                .unwrap();
        }

        assert_eq!(app_window_count(app.handle()), 2);
    }

    #[test]
    fn the_message_says_how_many_app_windows_will_close() {
        assert!(quit_copy(true, 3).message.contains("3 个应用窗口"));
        assert!(quit_copy(false, 3).message.contains("3 app windows"));
        assert!(quit_copy(false, 1).message.contains("1 app window,"));
    }

    #[test]
    fn the_buttons_say_what_they_do() {
        assert_eq!(
            (quit_copy(true, 1).quit, quit_copy(true, 1).cancel),
            ("退出", "取消")
        );
        assert_eq!(
            (quit_copy(false, 1).quit, quit_copy(false, 1).cancel),
            ("Quit", "Cancel")
        );
    }
}
