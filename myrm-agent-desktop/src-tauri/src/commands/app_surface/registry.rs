//! 应用在桌面上登记的东西：托盘项与全局快捷键，按应用归属。
//!
//! [INPUT]
//! - 应用窗口命令传来的产物 ID 与快捷键（已规范化）
//!
//! [OUTPUT]
//! - AppSurfaceRegistry: 记录哪些应用有托盘项、哪个应用占了哪个全局快捷键
//! - Claim: 占用快捷键的结果（新占用 / 已是本应用的 / 被他人占用）
//!
//! [POS]
//! Tauri 托管状态，纯数据，不碰窗口与插件，所以占用、冲突、释放的规则可以直接单测。
//! 快捷键以插件的展示写法作键（`display_key`），全局事件分发据此找到该把事件发给哪个应用；
//! 登记表在 `update_global_shortcut` 重注册全部快捷键之后用来恢复应用的快捷键。

use std::collections::{HashMap, HashSet};
use std::sync::{Mutex, MutexGuard};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OwnedShortcut {
    pub artifact_id: String,
    /// 页面注册时的规范写法，事件原样发回给页面。
    pub accelerator: String,
}

/// 一个应用最多同时占用的全局快捷键数；与服务端声明上限（`MAX_SHORTCUTS`）同值，这里是第二道闸。
pub const MAX_SHORTCUTS_PER_APP: usize = 4;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Claim {
    Fresh,
    AlreadyOwned,
    TakenByAnother,
    LimitReached,
}

#[derive(Default)]
struct Inner {
    trays: HashSet<String>,
    shortcuts: HashMap<String, OwnedShortcut>,
}

#[derive(Default)]
pub struct AppSurfaceRegistry {
    inner: Mutex<Inner>,
}

impl AppSurfaceRegistry {
    fn lock(&self) -> MutexGuard<'_, Inner> {
        // 持锁期间只有集合操作，不会 panic；即便毒化，数据仍是一致的。
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn mark_tray(&self, artifact_id: &str) {
        self.lock().trays.insert(artifact_id.to_string());
    }

    pub fn unmark_tray(&self, artifact_id: &str) -> bool {
        self.lock().trays.remove(artifact_id)
    }

    pub fn has_tray(&self, artifact_id: &str) -> bool {
        self.lock().trays.contains(artifact_id)
    }

    pub fn claim_shortcut(&self, key: &str, artifact_id: &str, accelerator: &str) -> Claim {
        let mut inner = self.lock();
        match inner.shortcuts.get(key) {
            Some(owner) if owner.artifact_id == artifact_id => Claim::AlreadyOwned,
            Some(_) => Claim::TakenByAnother,
            None => {
                let held = inner
                    .shortcuts
                    .values()
                    .filter(|owner| owner.artifact_id == artifact_id)
                    .count();
                if held >= MAX_SHORTCUTS_PER_APP {
                    return Claim::LimitReached;
                }
                inner.shortcuts.insert(
                    key.to_string(),
                    OwnedShortcut {
                        artifact_id: artifact_id.to_string(),
                        accelerator: accelerator.to_string(),
                    },
                );
                Claim::Fresh
            }
        }
    }

    /// 只释放本应用占的；他人占的不动。返回是否真的释放了。
    pub fn release_shortcut(&self, key: &str, artifact_id: &str) -> bool {
        let mut inner = self.lock();
        if inner
            .shortcuts
            .get(key)
            .is_some_and(|owner| owner.artifact_id == artifact_id)
        {
            inner.shortcuts.remove(key);
            return true;
        }
        false
    }

    /// 释放本应用的全部快捷键，返回它们的规范写法（调用方据此向插件注销）。
    pub fn release_all_shortcuts(&self, artifact_id: &str) -> Vec<String> {
        let mut inner = self.lock();
        let mut released = Vec::new();
        inner.shortcuts.retain(|_, owner| {
            let mine = owner.artifact_id == artifact_id;
            if mine {
                released.push(owner.accelerator.clone());
            }
            !mine
        });
        released
    }

    pub fn shortcut_owner(&self, key: &str) -> Option<OwnedShortcut> {
        self.lock().shortcuts.get(key).cloned()
    }

    pub fn all_shortcuts(&self) -> Vec<(String, OwnedShortcut)> {
        self.lock()
            .shortcuts
            .iter()
            .map(|(key, owner)| (key.clone(), owner.clone()))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::{AppSurfaceRegistry, Claim, MAX_SHORTCUTS_PER_APP};

    #[test]
    fn a_combination_belongs_to_the_first_app_that_claims_it() {
        let registry = AppSurfaceRegistry::default();

        assert_eq!(registry.claim_shortcut("k", "a", "Alt+K"), Claim::Fresh);
        assert_eq!(
            registry.claim_shortcut("k", "a", "Alt+K"),
            Claim::AlreadyOwned
        );
        assert_eq!(
            registry.claim_shortcut("k", "b", "Alt+K"),
            Claim::TakenByAnother
        );
        assert_eq!(registry.shortcut_owner("k").unwrap().artifact_id, "a");
    }

    #[test]
    fn an_app_can_only_release_its_own_combination() {
        let registry = AppSurfaceRegistry::default();
        registry.claim_shortcut("k", "a", "Alt+K");

        assert!(!registry.release_shortcut("k", "b"));
        assert!(registry.shortcut_owner("k").is_some());
        assert!(registry.release_shortcut("k", "a"));
        assert!(registry.shortcut_owner("k").is_none());
        assert!(!registry.release_shortcut("k", "a"));
    }

    #[test]
    fn releasing_everything_of_one_app_leaves_the_others() {
        let registry = AppSurfaceRegistry::default();
        registry.claim_shortcut("k1", "a", "Alt+1");
        registry.claim_shortcut("k2", "a", "Alt+2");
        registry.claim_shortcut("k3", "b", "Alt+3");

        let mut released = registry.release_all_shortcuts("a");
        released.sort();

        assert_eq!(released, ["Alt+1", "Alt+2"]);
        assert_eq!(registry.all_shortcuts().len(), 1);
        assert!(registry.shortcut_owner("k3").is_some());
    }

    #[test]
    fn an_app_cannot_hold_more_than_its_share_of_combinations() {
        let registry = AppSurfaceRegistry::default();
        for n in 0..MAX_SHORTCUTS_PER_APP {
            assert_eq!(
                registry.claim_shortcut(&format!("k{n}"), "a", "Alt+K"),
                Claim::Fresh
            );
        }

        assert_eq!(
            registry.claim_shortcut("extra", "a", "Alt+K"),
            Claim::LimitReached
        );
        assert_eq!(registry.claim_shortcut("extra", "b", "Alt+K"), Claim::Fresh);
        registry.release_shortcut("k0", "a");
        assert_eq!(
            registry.claim_shortcut("extra2", "a", "Alt+K"),
            Claim::Fresh
        );
    }

    #[test]
    fn tray_membership_follows_mark_and_unmark() {
        let registry = AppSurfaceRegistry::default();

        assert!(!registry.has_tray("a"));
        registry.mark_tray("a");
        assert!(registry.has_tray("a"));
        assert!(registry.unmark_tray("a"));
        assert!(!registry.unmark_tray("a"));
        assert!(!registry.has_tray("a"));
    }
}
