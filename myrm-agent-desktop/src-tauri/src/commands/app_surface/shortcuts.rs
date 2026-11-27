//! 应用的全局快捷键：占用、注销、触发分发、全局重置后的恢复。
//!
//! [INPUT]
//! - registry::AppSurfaceRegistry (POS: 谁占了哪个组合)
//! - accelerator (POS: 写法的规范化与解析)
//! - tauri_plugin_global_shortcut (POS: 操作系统级注册，与 Myrm 自身四个全局入口共用一个插件)
//!
//! [OUTPUT]
//! - register_with / unregister_with / restore_with: 对「向插件注册、注销」参数化的核心流程，测试直接驱动
//! - register / unregister / unregister_all / restore / dispatch: 绑定到真实插件与窗口的封装
//! - SHORTCUT_EVENT: 组合被按下时发给应用窗口的事件名，载荷 `{ accelerator }`
//!
//! [POS]
//! 冲突一律拒绝而不覆盖：组合已被别的应用、Myrm 自身入口或系统占用时，页面得到 `conflict`。
//! Myrm 自身入口先于任何应用注册，插件对重复注册会报错，所以不需要另维护一份「保留组合」清单；
//! 但自身入口被用户改成某个应用已占的组合时，`update_global_shortcut` 的整体重注册会让应用丢掉该组合，
//! `restore` 把能恢复的恢复回来，恢复不了的从登记表移除。

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut};

use super::accelerator::{display_key, normalize_accelerator, parse_shortcut};
use super::registry::{AppSurfaceRegistry, Claim};
use super::window_label;

pub const SHORTCUT_EVENT: &str = "app-surface:shortcut";

/// 页面据此识别「组合被占用」，与前端 `lib/desktop/appSurface.ts` 的比较同源。
pub const CONFLICT: &str = "conflict";

#[derive(Serialize, Clone)]
struct ShortcutPayload<'a> {
    accelerator: &'a str,
}

pub fn register_with<F>(
    registry: &AppSurfaceRegistry,
    artifact_id: &str,
    accelerator: &str,
    register: F,
) -> Result<(), String>
where
    F: FnOnce(Shortcut) -> Result<(), String>,
{
    let canonical = normalize_accelerator(accelerator)?;
    let shortcut = parse_shortcut(&canonical)?;
    let key = display_key(&shortcut);
    match registry.claim_shortcut(&key, artifact_id, &canonical) {
        Claim::AlreadyOwned => Ok(()),
        Claim::TakenByAnother => Err(CONFLICT.to_string()),
        Claim::LimitReached => Err("This app already holds the most key combinations".to_string()),
        Claim::Fresh => register(shortcut).map_err(|error| {
            registry.release_shortcut(&key, artifact_id);
            eprintln!("[app-surface] {canonical} could not be registered: {error}");
            CONFLICT.to_string()
        }),
    }
}

/// `accelerator` 为空表示注销本应用的全部组合。
pub fn unregister_with<F>(
    registry: &AppSurfaceRegistry,
    artifact_id: &str,
    accelerator: Option<&str>,
    mut unregister: F,
) -> Result<(), String>
where
    F: FnMut(Shortcut),
{
    let Some(accelerator) = accelerator else {
        for canonical in registry.release_all_shortcuts(artifact_id) {
            unregister(parse_shortcut(&canonical)?);
        }
        return Ok(());
    };
    let canonical = normalize_accelerator(accelerator)?;
    let shortcut = parse_shortcut(&canonical)?;
    if registry.release_shortcut(&display_key(&shortcut), artifact_id) {
        unregister(shortcut);
    }
    Ok(())
}

/// 把登记表里的组合逐个重新注册；注册不上的从登记表移除，返回它们的规范写法。
pub fn restore_with<F>(registry: &AppSurfaceRegistry, mut register: F) -> Vec<String>
where
    F: FnMut(Shortcut) -> Result<(), String>,
{
    let mut lost = Vec::new();
    for (key, owner) in registry.all_shortcuts() {
        let restored = parse_shortcut(&owner.accelerator).and_then(&mut register);
        if let Err(error) = restored {
            eprintln!("[app-surface] {} is gone: {error}", owner.accelerator);
            registry.release_shortcut(&key, &owner.artifact_id);
            lost.push(owner.accelerator);
        }
    }
    lost
}

pub fn register(
    app: &AppHandle,
    registry: &AppSurfaceRegistry,
    artifact_id: &str,
    accelerator: &str,
) -> Result<(), String> {
    register_with(registry, artifact_id, accelerator, |shortcut| {
        app.global_shortcut()
            .register(shortcut)
            .map_err(|e| e.to_string())
    })
}

pub fn unregister(
    app: &AppHandle,
    registry: &AppSurfaceRegistry,
    artifact_id: &str,
    accelerator: Option<&str>,
) -> Result<(), String> {
    unregister_with(registry, artifact_id, accelerator, |shortcut| {
        if let Err(error) = app.global_shortcut().unregister(shortcut) {
            eprintln!("[app-surface] unregistering a shortcut failed: {error}");
        }
    })
}

/// 应用窗口销毁、授权收回或卸载时调用：本应用占的组合全部归还。
pub fn unregister_all(app: &AppHandle, registry: &AppSurfaceRegistry, artifact_id: &str) {
    let _ = unregister(app, registry, artifact_id, None);
}

/// `update_global_shortcut` 注销全部组合后调用，把应用的组合补回去。
pub fn restore(app: &AppHandle) {
    let registry = app.state::<AppSurfaceRegistry>();
    restore_with(&registry, |shortcut| {
        app.global_shortcut()
            .register(shortcut)
            .map_err(|e| e.to_string())
    });
}

/// 被应用占用的组合按下时把事件发给该应用的窗口；返回是否已被应用接管（接管后不再走 Myrm 自身入口）。
pub fn dispatch(app: &AppHandle, shortcut: &Shortcut, pressed: bool) -> bool {
    let registry = app.state::<AppSurfaceRegistry>();
    let Some(owner) = registry.shortcut_owner(&display_key(shortcut)) else {
        return false;
    };
    if pressed {
        let label = window_label(&owner.artifact_id);
        let payload = ShortcutPayload {
            accelerator: &owner.accelerator,
        };
        if let Err(error) = app.emit_to(label, SHORTCUT_EVENT, payload) {
            eprintln!("[app-surface] delivering a shortcut failed: {error}");
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;

    use super::{register_with, restore_with, unregister_with, CONFLICT};
    use crate::commands::app_surface::registry::AppSurfaceRegistry;

    #[test]
    fn a_free_combination_is_registered_with_the_plugin_once() {
        let registry = AppSurfaceRegistry::default();
        let calls = RefCell::new(0);

        for _ in 0..2 {
            let done = register_with(&registry, "a", "shift+commandorcontrol+k", |_| {
                *calls.borrow_mut() += 1;
                Ok(())
            });
            assert_eq!(done, Ok(()));
        }

        assert_eq!(*calls.borrow(), 1);
        assert_eq!(registry.all_shortcuts().len(), 1);
    }

    #[test]
    fn a_combination_another_app_holds_is_a_conflict_and_never_reaches_the_plugin() {
        let registry = AppSurfaceRegistry::default();
        register_with(&registry, "a", "Alt+K", |_| Ok(())).unwrap();

        let result = register_with(&registry, "b", "alt+k", |_| panic!("must not register"));

        assert_eq!(result, Err(CONFLICT.to_string()));
        assert_eq!(registry.all_shortcuts()[0].1.artifact_id, "a");
    }

    #[test]
    fn a_combination_the_system_refuses_is_a_conflict_and_is_not_kept() {
        let registry = AppSurfaceRegistry::default();

        let result = register_with(&registry, "a", "Alt+K", |_| Err("taken".to_string()));

        assert_eq!(result, Err(CONFLICT.to_string()));
        assert!(registry.all_shortcuts().is_empty());
        assert_eq!(register_with(&registry, "a", "Alt+K", |_| Ok(())), Ok(()));
    }

    #[test]
    fn an_unsupported_combination_is_refused_before_anything_is_claimed() {
        let registry = AppSurfaceRegistry::default();

        let result = register_with(&registry, "a", "K", |_| panic!("must not register"));

        assert!(result.is_err());
        assert_ne!(result, Err(CONFLICT.to_string()));
        assert!(registry.all_shortcuts().is_empty());
    }

    #[test]
    fn unregistering_one_combination_leaves_the_apps_others_and_foreign_ones() {
        let registry = AppSurfaceRegistry::default();
        register_with(&registry, "a", "Alt+1", |_| Ok(())).unwrap();
        register_with(&registry, "a", "Alt+2", |_| Ok(())).unwrap();
        let dropped = RefCell::new(0);

        unregister_with(&registry, "a", Some("alt+1"), |_| {
            *dropped.borrow_mut() += 1
        })
        .unwrap();
        unregister_with(&registry, "b", Some("Alt+2"), |_| {
            *dropped.borrow_mut() += 1
        })
        .unwrap();

        assert_eq!(*dropped.borrow(), 1);
        assert_eq!(registry.all_shortcuts().len(), 1);
    }

    #[test]
    fn unregistering_without_a_combination_gives_back_everything_the_app_holds() {
        let registry = AppSurfaceRegistry::default();
        register_with(&registry, "a", "Alt+1", |_| Ok(())).unwrap();
        register_with(&registry, "a", "Alt+2", |_| Ok(())).unwrap();
        register_with(&registry, "b", "Alt+3", |_| Ok(())).unwrap();
        let dropped = RefCell::new(0);

        unregister_with(&registry, "a", None, |_| *dropped.borrow_mut() += 1).unwrap();

        assert_eq!(*dropped.borrow(), 2);
        assert_eq!(registry.all_shortcuts().len(), 1);
    }

    #[test]
    fn after_a_global_reset_what_can_be_registered_again_stays_and_the_rest_is_dropped() {
        let registry = AppSurfaceRegistry::default();
        register_with(&registry, "a", "Alt+1", |_| Ok(())).unwrap();
        register_with(&registry, "b", "Alt+2", |_| Ok(())).unwrap();

        let lost = restore_with(&registry, |shortcut| {
            if format!("{shortcut}").contains("Digit2") {
                Err("now taken".to_string())
            } else {
                Ok(())
            }
        });

        assert_eq!(lost, ["Alt+2"]);
        assert_eq!(registry.all_shortcuts().len(), 1);
        assert_eq!(registry.all_shortcuts()[0].1.artifact_id, "a");
    }
}
