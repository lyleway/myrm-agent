//! 原生界面（对话框、托盘菜单）用的语言判断。
//!
//! [INPUT]
//! - 环境变量 `MYRM_UI_LOCALE` / `MYRM_LOCALE` / `LC_ALL` / `LC_MESSAGES` / `LANG`（按此顺序取第一个能判断的值）
//!
//! [OUTPUT]
//! - is_zh_locale_from: 按候选值（先到先得）判断是否用中文；以 `zh` 开头为中文，以 `en` 开头为英文，其余值跳过
//! - ui_locale_is_zh: 读取当前进程环境后的判断
//!
//! [POS]
//! 原生层没有前端的 i18n，原生文案（敏感操作确认、退出确认、应用托盘菜单）各自带中英两份，
//! 用这里的判断选一份；判断不出时用英文。
//! 前端界面语言不经此处：它是用户在设置里选的，而这里只看进程环境。

/// 候选值按顺序判断：第一个以 `zh` 开头的为中文，第一个以 `en` 开头的为英文；空值与其他值（如 `C`）跳过。
pub fn is_zh_locale_from<I>(candidates: I) -> bool
where
    I: IntoIterator<Item = Option<String>>,
{
    for candidate in candidates.into_iter().flatten() {
        let normalized = candidate.trim().replace('_', "-").to_ascii_lowercase();
        if normalized.starts_with("zh") {
            return true;
        }
        if normalized.starts_with("en") {
            return false;
        }
    }
    false
}

pub fn ui_locale_is_zh() -> bool {
    is_zh_locale_from(
        [
            "MYRM_UI_LOCALE",
            "MYRM_LOCALE",
            "LC_ALL",
            "LC_MESSAGES",
            "LANG",
        ]
        .into_iter()
        .map(|name| std::env::var(name).ok()),
    )
}

#[cfg(test)]
mod tests {
    use super::is_zh_locale_from;

    fn from(values: &[Option<&str>]) -> bool {
        is_zh_locale_from(values.iter().map(|value| value.map(str::to_string)))
    }

    #[test]
    fn the_first_decisive_candidate_wins() {
        assert!(from(&[Some("zh_CN.UTF-8"), Some("en_US")]));
        assert!(!from(&[Some("en_US.UTF-8"), Some("zh_CN")]));
    }

    #[test]
    fn empty_and_undecisive_candidates_are_skipped() {
        assert!(from(&[None, Some("  "), Some("C"), Some("zh-Hans")]));
        assert!(!from(&[Some("POSIX"), Some("en")]));
    }

    #[test]
    fn regional_variants_follow_their_language() {
        assert!(from(&[Some("zh-CN")]));
        assert!(from(&[Some("zh_Hant_TW")]));
        assert!(!from(&[Some("en-US")]));
        assert!(!from(&[Some("ja-JP"), Some("unknown")]));
    }

    #[test]
    fn nothing_decisive_means_english() {
        assert!(!from(&[]));
        assert!(!from(&[None, Some("C")]));
    }
}
