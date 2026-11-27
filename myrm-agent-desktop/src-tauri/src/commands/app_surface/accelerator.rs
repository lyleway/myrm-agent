//! 应用声明的全局快捷键写法：规范化与解析。
//!
//! [INPUT]
//! - 页面经宿主页传来的快捷键字符串（不可信输入）
//!
//! [OUTPUT]
//! - normalize_accelerator: 规范写法 `Modifier+...+Key`（如 `CommandOrControl+Shift+K`），不接受的写法返回错误
//! - parse_shortcut: 规范写法 → 全局快捷键插件的 `Shortcut`
//! - display_key: 插件对该组合的展示写法，登记表与全局事件分发都用它作键
//!
//! [POS]
//! 与服务端 `capabilities.normalize_accelerator`、前端 `lib/app-bridge/accelerator.ts` 是同一条规则：
//! 授权界面上用户读到的组合就是被注册的组合。必须带 Shift 以外的修饰键，页面不能借此接管普通打字；
//! 键只收字母、数字与 F1–F12，修饰键只收 CommandOrControl / Control / Alt / Shift 四个写法。

use std::str::FromStr;

use tauri_plugin_global_shortcut::Shortcut;

const MODIFIERS: [&str; 4] = ["CommandOrControl", "Control", "Alt", "Shift"];

fn is_allowed_key(key: &str) -> bool {
    let mut chars = key.chars();
    match (chars.next(), chars.next()) {
        (Some(c), None) => c.is_ascii_uppercase() || c.is_ascii_digit(),
        (Some('F'), Some(_)) => key[1..]
            .parse::<u8>()
            .is_ok_and(|n| (1..=12).contains(&n) && !key[1..].starts_with('0')),
        _ => false,
    }
}

pub fn normalize_accelerator(raw: &str) -> Result<String, String> {
    let invalid = || format!("Not a supported key combination: {raw:?}");
    let mut parts: Vec<&str> = raw.split('+').map(str::trim).collect();
    let key = parts.pop().unwrap_or_default().to_ascii_uppercase();
    if !is_allowed_key(&key) || parts.is_empty() {
        return Err(invalid());
    }
    let mut chosen: Vec<&str> = Vec::new();
    for part in parts {
        let modifier = *MODIFIERS
            .iter()
            .find(|name| name.eq_ignore_ascii_case(part))
            .ok_or_else(invalid)?;
        if chosen.contains(&modifier) {
            return Err(invalid());
        }
        chosen.push(modifier);
    }
    if chosen.iter().all(|name| *name == "Shift") {
        return Err(invalid());
    }
    let mut canonical: Vec<&str> = MODIFIERS
        .iter()
        .copied()
        .filter(|name| chosen.contains(name))
        .collect();
    canonical.push(&key);
    Ok(canonical.join("+"))
}

pub fn parse_shortcut(canonical: &str) -> Result<Shortcut, String> {
    Shortcut::from_str(canonical).map_err(|e| format!("Invalid shortcut {canonical:?}: {e}"))
}

pub fn display_key(shortcut: &Shortcut) -> String {
    format!("{shortcut}")
}

#[cfg(test)]
mod tests {
    use super::{display_key, normalize_accelerator, parse_shortcut};

    #[test]
    fn spells_a_combination_canonically() {
        for (raw, canonical) in [
            ("commandorcontrol+k", "CommandOrControl+K"),
            (" Control + Alt + 7 ", "Control+Alt+7"),
            ("Shift+Alt+F12", "Alt+Shift+F12"),
            ("alt+f1", "Alt+F1"),
        ] {
            assert_eq!(
                normalize_accelerator(raw).as_deref(),
                Ok(canonical),
                "{raw}"
            );
        }
    }

    #[test]
    fn refuses_what_could_take_over_typing_or_is_not_supported() {
        for raw in [
            "K",
            "Shift+K",
            "Alt",
            "Alt+",
            "Alt+KK",
            "Alt+F13",
            "Alt+F0",
            "Alt+F01",
            "Super+K",
            "Cmd+K",
            "Alt+Alt+K",
            "Alt+Space",
            "Alt+é",
            "",
            "+",
            "Alt++K",
        ] {
            assert!(normalize_accelerator(raw).is_err(), "{raw:?} was accepted");
        }
    }

    #[test]
    fn every_canonical_form_is_something_the_plugin_can_register() {
        for raw in [
            "CommandOrControl+Shift+K",
            "Control+Alt+7",
            "Alt+F12",
            "CommandOrControl+F1",
            "Control+Shift+Z",
        ] {
            let canonical = normalize_accelerator(raw).unwrap();
            assert!(parse_shortcut(&canonical).is_ok(), "{canonical}");
        }
    }

    #[test]
    fn two_spellings_of_one_combination_have_one_key() {
        let a =
            parse_shortcut(&normalize_accelerator("shift+commandorcontrol+k").unwrap()).unwrap();
        let b = parse_shortcut("CommandOrControl+Shift+K").unwrap();
        assert_eq!(display_key(&a), display_key(&b));
    }
}
