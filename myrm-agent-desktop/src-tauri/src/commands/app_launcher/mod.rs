//! 已安装应用的系统入口：Spotlight / 开始菜单 / 应用菜单里的启动器。
//!
//! [INPUT]
//! - plan (POS: 各平台启动器的文件与标记，纯数据)
//! - store (POS: 在给定目录下写入、查找、移除启动器)
//! - Tauri path resolver (POS: 用户主目录与数据目录，决定启动器放哪)
//!
//! [OUTPUT]
//! - has_app_launcher / create_app_launcher / remove_app_launcher: IPC 命令，按产物 ID 管理一个启动器
//! - launcher_dir: 各平台放启动器的目录
//!
//! [POS]
//! 启动器只负责打开 `myrmagent://app/<id>`，由主应用的深链接分发开应用窗口（主应用没在运行时，系统会先把它拉起）。
//! 本地生成、不带隔离标记，所以 macOS 不经公证也能打开（见 roadmap 的真机核对记录）；入口只放在用户自己的目录里，
//! 不写系统目录、不需要提权。主窗口与应用窗口可调用：应用窗口只能管理自己的启动器（标签推出的 ID 必须与参数相同）。

mod plan;
mod store;

use std::path::{Path, PathBuf};

use tauri::{AppHandle, Manager, WebviewWindow};

use self::plan::{build_plan, Platform};
use super::app_surface::artifact_id_of;

const DIR_NAME: &str = "Myrm Apps";

#[cfg(target_os = "macos")]
const MAC_ICON: Option<&[u8]> = Some(include_bytes!("../../../icons/icon.icns"));
#[cfg(not(target_os = "macos"))]
const MAC_ICON: Option<&[u8]> = None;

pub fn launcher_dir(platform: Platform, home: &Path, data: &Path) -> PathBuf {
    match platform {
        Platform::MacOs => home.join("Applications").join(DIR_NAME),
        Platform::Windows => data
            .join("Microsoft")
            .join("Windows")
            .join("Start Menu")
            .join("Programs")
            .join(DIR_NAME),
        Platform::Linux => data.join("applications").join("myrm-apps"),
    }
}

fn resolve_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let paths = app.path();
    let home = paths.home_dir().map_err(|e| e.to_string())?;
    let data = paths.data_dir().map_err(|e| e.to_string())?;
    Ok(launcher_dir(Platform::current(), &home, &data))
}

/// 校验调用方：ID 要是合法产物 ID；应用窗口只能操作自己的。
fn authorize(window: &WebviewWindow, artifact_id: &str) -> Result<(), String> {
    if !super::app_window::is_valid_artifact_id(artifact_id) {
        return Err("Invalid app id".to_string());
    }
    match artifact_id_of(window.label()) {
        Some(own) if own != artifact_id => {
            Err("An app window can only manage its own launcher".to_string())
        }
        _ => Ok(()),
    }
}

#[tauri::command]
pub fn has_app_launcher(
    app: AppHandle,
    window: WebviewWindow,
    artifact_id: String,
) -> Result<bool, String> {
    authorize(&window, &artifact_id)?;
    let plan = build_plan(Platform::current(), &artifact_id, "", None);
    Ok(!store::find(&resolve_dir(&app)?, &plan).is_empty())
}

#[tauri::command]
pub fn create_app_launcher(
    app: AppHandle,
    window: WebviewWindow,
    artifact_id: String,
    name: String,
) -> Result<(), String> {
    authorize(&window, &artifact_id)?;
    let plan = build_plan(Platform::current(), &artifact_id, &name, MAC_ICON);
    store::install(&resolve_dir(&app)?, &artifact_id, &name, &plan).map(|_| ())
}

#[tauri::command]
pub fn remove_app_launcher(
    app: AppHandle,
    window: WebviewWindow,
    artifact_id: String,
) -> Result<(), String> {
    authorize(&window, &artifact_id)?;
    let plan = build_plan(Platform::current(), &artifact_id, "", None);
    store::remove(&resolve_dir(&app)?, &plan).map(|_| ())
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::{launcher_dir, Platform};

    #[test]
    fn each_platform_keeps_its_launchers_in_the_users_own_place() {
        let home = Path::new("/home/u");
        let data = Path::new("/data");

        assert_eq!(
            launcher_dir(Platform::MacOs, home, data),
            Path::new("/home/u/Applications/Myrm Apps")
        );
        assert_eq!(
            launcher_dir(Platform::Windows, home, data),
            Path::new("/data/Microsoft/Windows/Start Menu/Programs/Myrm Apps")
        );
        assert_eq!(
            launcher_dir(Platform::Linux, home, data),
            Path::new("/data/applications/myrm-apps")
        );
    }
}
