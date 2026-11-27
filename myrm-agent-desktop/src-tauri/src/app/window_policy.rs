//! 应用级窗口策略：窗口被要求关闭、被销毁时，应用该做什么（纯决策，不触碰窗口）。
//!
//! [INPUT]
//! - 窗口标签、「关闭到托盘」设置值与应用窗口的现状（由 `app::setup` 的窗口事件处理传入；本文件不依赖窗口句柄与应用状态）
//!
//! [OUTPUT]
//! - CloseAction: 窗口收到关闭请求时的处置（`HideToTray` 藏起主窗口 / `HideApp` 藏起有托盘项的应用窗口 /
//!   `ConfirmQuit` 拦下关闭并走退出确认 / `Proceed` 放行）
//! - AppWindows: 应用窗口的现状（该窗口的应用有无托盘项、一共开着几个应用窗口）
//! - on_close_requested: 按窗口标签、「关闭到托盘」设置与应用窗口现状给出处置
//! - destroyed_stops_app: 该窗口被销毁时是否意味着应用退出
//!
//! [POS]
//! 「关闭到托盘」与「窗口销毁即停机」承诺的是主窗口：设置文案只说"关闭主窗口时应用将在后台继续运行"。
//! 会话窗、宠物层、应用窗、帷幕与审批高亮都是按需建、用完即销的从属窗口，被关闭就是被关闭——
//! 若被拦成永不销毁的隐藏窗，会把 macOS 切成 Accessory（主窗口仍开着，Dock 图标却消失）；
//! 若销毁被当作应用退出，会把前后端一并停掉。
//! 唯一的例外是有托盘项的应用窗口：用户授权了它常驻，关闭只是藏起来（不碰 macOS 的激活策略，
//! 主窗口的 Dock 图标不受影响），真正退出走托盘菜单的「退出应用」。
//! 主窗口在「关闭到托盘」关着时的关闭就是退出主进程，应用窗口因此一并消失，所以此时要先走退出确认。

use crate::commands::app_surface::artifact_id_of;

const MAIN_WINDOW_LABEL: &str = "main";

/// 窗口收到关闭请求时的处置。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum CloseAction {
    /// 拦截关闭，把主窗口藏进托盘，应用继续在后台运行。
    HideToTray,
    /// 拦截关闭，只把有托盘项的应用窗口藏起来。
    HideApp,
    /// 拦截关闭，改走退出确认（确认后才真的退出）。
    ConfirmQuit,
    /// 放行，窗口按系统语义正常销毁。
    Proceed,
}

/// 应用窗口的现状。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct AppWindows {
    /// 被要求关闭的这个窗口，它的应用有托盘项。
    pub closing_app_has_tray: bool,
    /// 当前开着的应用窗口数（含藏起来的）。
    pub open: usize,
}

pub(super) fn on_close_requested(
    label: &str,
    close_to_tray: bool,
    app_windows: AppWindows,
) -> CloseAction {
    if label == MAIN_WINDOW_LABEL {
        return match (close_to_tray, app_windows.open) {
            (true, _) => CloseAction::HideToTray,
            (false, 0) => CloseAction::Proceed,
            (false, _) => CloseAction::ConfirmQuit,
        };
    }
    if app_windows.closing_app_has_tray && artifact_id_of(label).is_some() {
        CloseAction::HideApp
    } else {
        CloseAction::Proceed
    }
}

/// 该窗口被销毁时是否意味着应用退出（走优雅停机并结束进程）。
pub(super) fn destroyed_stops_app(label: &str) -> bool {
    label == MAIN_WINDOW_LABEL
}

#[cfg(test)]
mod tests {
    use super::{destroyed_stops_app, on_close_requested, AppWindows, CloseAction};

    const SECONDARY_LABELS: [&str; 7] = [
        "pet-surface",
        "session-42",
        "app-7",
        "privacy-curtain-1-0",
        "privacy-curtain-7-2",
        "visual-approval-overlay-0",
        "visual-approval-overlay-31",
    ];

    const NO_APPS: AppWindows = AppWindows {
        closing_app_has_tray: false,
        open: 0,
    };

    fn apps(closing_app_has_tray: bool, open: usize) -> AppWindows {
        AppWindows {
            closing_app_has_tray,
            open,
        }
    }

    #[test]
    fn main_window_hides_to_tray_only_when_the_setting_is_on() {
        assert_eq!(
            on_close_requested("main", true, NO_APPS),
            CloseAction::HideToTray
        );
        assert_eq!(
            on_close_requested("main", false, NO_APPS),
            CloseAction::Proceed
        );
    }

    #[test]
    fn closing_the_main_window_for_good_asks_first_when_app_windows_would_go_with_it() {
        assert_eq!(
            on_close_requested("main", false, apps(false, 2)),
            CloseAction::ConfirmQuit
        );
        // 关闭到托盘开着时主窗口只是藏起来，应用窗口不受影响，不需要确认。
        assert_eq!(
            on_close_requested("main", true, apps(false, 2)),
            CloseAction::HideToTray
        );
    }

    #[test]
    fn closing_a_secondary_window_never_hides_it_into_the_tray() {
        for label in SECONDARY_LABELS {
            for close_to_tray in [true, false] {
                assert_eq!(
                    on_close_requested(label, close_to_tray, NO_APPS),
                    CloseAction::Proceed,
                    "{label} close_to_tray={close_to_tray}"
                );
            }
        }
    }

    #[test]
    fn an_app_window_with_a_tray_item_is_hidden_and_any_other_is_closed() {
        for close_to_tray in [true, false] {
            assert_eq!(
                on_close_requested("app-7", close_to_tray, apps(true, 1)),
                CloseAction::HideApp
            );
            assert_eq!(
                on_close_requested("app-7", close_to_tray, apps(false, 1)),
                CloseAction::Proceed
            );
        }
    }

    #[test]
    fn a_tray_item_never_keeps_a_window_that_is_not_an_app_window_alive() {
        for label in ["session-42", "pet-surface", "privacy-curtain-1-0"] {
            assert_eq!(
                on_close_requested(label, true, apps(true, 1)),
                CloseAction::Proceed,
                "{label}"
            );
        }
    }

    #[test]
    fn only_the_main_window_destroy_stops_the_app() {
        assert!(destroyed_stops_app("main"));
        for label in SECONDARY_LABELS {
            assert!(!destroyed_stops_app(label), "{label}");
        }
    }
}
