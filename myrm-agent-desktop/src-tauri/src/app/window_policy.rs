//! 应用级窗口策略：窗口被要求关闭、被销毁时，应用该做什么（纯决策，不触碰窗口）。
//!
//! 「关闭到托盘」与「窗口销毁即停机」承诺的是主窗口：设置文案只说"关闭主窗口时应用将在后台继续运行"。
//! 会话窗、宠物层、应用窗、帷幕与审批高亮都是按需建、用完即销的从属窗口，被关闭就是被关闭——
//! 若被拦成永不销毁的隐藏窗，会把 macOS 切成 Accessory（主窗口仍开着，Dock 图标却消失）；
//! 若销毁被当作应用退出，会把前后端一并停掉。

const MAIN_WINDOW_LABEL: &str = "main";

/// 窗口收到关闭请求时的处置。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum CloseAction {
    /// 拦截关闭，把主窗口藏进托盘，应用继续在后台运行。
    HideToTray,
    /// 放行，窗口按系统语义正常销毁。
    Proceed,
}

pub(super) fn on_close_requested(label: &str, close_to_tray: bool) -> CloseAction {
    if label == MAIN_WINDOW_LABEL && close_to_tray {
        CloseAction::HideToTray
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
    use super::{destroyed_stops_app, on_close_requested, CloseAction};

    const SECONDARY_LABELS: [&str; 7] = [
        "pet-surface",
        "session-42",
        "app-7",
        "privacy-curtain-1-0",
        "privacy-curtain-7-2",
        "visual-approval-overlay-0",
        "visual-approval-overlay-31",
    ];

    #[test]
    fn main_window_hides_to_tray_only_when_the_setting_is_on() {
        assert_eq!(on_close_requested("main", true), CloseAction::HideToTray);
        assert_eq!(on_close_requested("main", false), CloseAction::Proceed);
    }

    #[test]
    fn closing_a_secondary_window_never_hides_it_into_the_tray() {
        for label in SECONDARY_LABELS {
            for close_to_tray in [true, false] {
                assert_eq!(
                    on_close_requested(label, close_to_tray),
                    CloseAction::Proceed,
                    "{label} close_to_tray={close_to_tray}"
                );
            }
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
