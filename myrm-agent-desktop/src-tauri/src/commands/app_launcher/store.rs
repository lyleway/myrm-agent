//! 把启动器写进磁盘 / 从磁盘找出、移除。
//!
//! [INPUT]
//! - plan::Plan (POS: 要写的文件与标记)
//!
//! [OUTPUT]
//! - install: 先清掉这个应用已有的入口，再写新的；返回入口路径
//! - find: 目录里属于这个应用的入口
//! - remove: 清掉这个应用的所有入口，返回是否真的清掉了东西
//!
//! [POS]
//! 目录由调用方给（各平台的「应用目录」），这里只在该目录下操作，且只碰标记匹配的入口：用户放在同目录的
//! 别的东西不读不改不删。写入先落在目录内的临时入口再改名，系统索引不会看到写了一半的启动器。

use std::fs;
use std::path::{Path, PathBuf};

use super::plan::{contains_marker, file_stem, Plan};

const TEMP_PREFIX: &str = ".myrm-launcher-";

fn marker_path(entry: &Path, plan: &Plan) -> PathBuf {
    if plan.marker_file.is_empty() {
        entry.to_path_buf()
    } else {
        entry.join(plan.marker_file)
    }
}

pub fn find(dir: &Path, plan: &Plan) -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir(dir) else {
        return Vec::new();
    };
    entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.ends_with(plan.extension) && !n.starts_with(TEMP_PREFIX))
        })
        .filter(|path| {
            fs::read_to_string(marker_path(path, plan))
                .is_ok_and(|text| contains_marker(&text, &plan.marker))
        })
        .collect()
}

fn remove_entry(path: &Path) -> std::io::Result<()> {
    if path.is_dir() {
        fs::remove_dir_all(path)
    } else {
        fs::remove_file(path)
    }
}

pub fn remove(dir: &Path, plan: &Plan) -> Result<bool, String> {
    let found = find(dir, plan);
    for path in &found {
        remove_entry(path).map_err(|e| format!("Could not remove {}: {e}", path.display()))?;
    }
    Ok(!found.is_empty())
}

fn write_entry(root: &Path, plan: &Plan) -> std::io::Result<()> {
    for file in &plan.files {
        let target = if file.relative.is_empty() {
            root.to_path_buf()
        } else {
            root.join(&file.relative)
        };
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(&target, &file.contents)?;
        #[cfg(unix)]
        if file.executable {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&target, fs::Permissions::from_mode(0o755))?;
        }
    }
    Ok(())
}

pub fn install(dir: &Path, artifact_id: &str, name: &str, plan: &Plan) -> Result<PathBuf, String> {
    let io = |e: std::io::Error| e.to_string();
    fs::create_dir_all(dir).map_err(io)?;
    remove(dir, plan)?;

    let stem = file_stem(name);
    let mut target = dir.join(format!("{stem}{}", plan.extension));
    if target.exists() {
        // 同名的是别的应用（本应用的旧入口刚清掉了）：带上 ID 前缀区分。
        let short: String = artifact_id.chars().take(6).collect();
        target = dir.join(format!("{stem} ({short}){}", plan.extension));
        if target.exists() {
            return Err(format!("{} already exists", target.display()));
        }
    }

    let staging = dir.join(format!("{TEMP_PREFIX}{artifact_id}{}", plan.extension));
    if staging.exists() {
        remove_entry(&staging).map_err(io)?;
    }
    if let Err(error) = write_entry(&staging, plan).and_then(|()| fs::rename(&staging, &target)) {
        let _ = remove_entry(&staging);
        return Err(error.to_string());
    }
    Ok(target)
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::{find, install, remove};
    use crate::commands::app_launcher::plan::{build_plan, Platform};

    fn plan(platform: Platform, id: &str, name: &str) -> crate::commands::app_launcher::plan::Plan {
        build_plan(platform, id, name, None)
    }

    #[test]
    fn a_launcher_is_written_whole_found_by_its_app_and_removed_again() {
        let dir = tempfile::tempdir().unwrap();
        let mac = plan(Platform::MacOs, "art-1", "Weather");

        let entry = install(dir.path(), "art-1", "Weather", &mac).unwrap();

        assert_eq!(entry.file_name().unwrap(), "Weather.app");
        assert!(entry.join("Contents/Info.plist").is_file());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(entry.join("Contents/MacOS/launcher"))
                .unwrap()
                .permissions()
                .mode();
            assert_eq!(
                mode & 0o111,
                0o111,
                "the launcher script must be executable"
            );
        }
        assert_eq!(find(dir.path(), &mac), [entry.clone()]);
        assert!(remove(dir.path(), &mac).unwrap());
        assert!(!entry.exists());
        assert!(!remove(dir.path(), &mac).unwrap());
    }

    #[test]
    fn installing_again_replaces_the_old_entry_even_after_the_app_was_renamed() {
        let dir = tempfile::tempdir().unwrap();
        let windows = plan(Platform::Windows, "art-1", "Old name");
        install(dir.path(), "art-1", "Old name", &windows).unwrap();

        let entry = install(dir.path(), "art-1", "New name", &windows).unwrap();

        assert_eq!(entry.file_name().unwrap(), "New name.url");
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 1);
    }

    #[test]
    fn two_apps_with_one_name_get_two_entries_and_removing_one_keeps_the_other() {
        let dir = tempfile::tempdir().unwrap();
        let one = plan(Platform::Linux, "aaaaaaaa-1", "Notes");
        let two = plan(Platform::Linux, "bbbbbbbb-2", "Notes");

        let first = install(dir.path(), "aaaaaaaa-1", "Notes", &one).unwrap();
        let second = install(dir.path(), "bbbbbbbb-2", "Notes", &two).unwrap();

        assert_eq!(first.file_name().unwrap(), "Notes.desktop");
        assert_eq!(second.file_name().unwrap(), "Notes (bbbbbb).desktop");
        assert!(remove(dir.path(), &one).unwrap());
        assert!(!first.exists());
        assert!(second.exists());
    }

    #[test]
    fn nothing_the_user_keeps_next_to_the_launchers_is_touched() {
        let dir = tempfile::tempdir().unwrap();
        let mine = plan(Platform::Windows, "art-1", "Weather");
        fs::write(
            dir.path().join("Weather.url"),
            "[InternetShortcut]\nURL=https://example.com\n",
        )
        .unwrap();
        fs::write(dir.path().join("notes.txt"), "keep").unwrap();

        let entry = install(dir.path(), "art-1", "Weather", &mine).unwrap();
        remove(dir.path(), &mine).unwrap();

        assert_eq!(entry.file_name().unwrap(), "Weather (art-1).url");
        assert!(dir.path().join("Weather.url").is_file());
        assert!(dir.path().join("notes.txt").is_file());
    }

    #[test]
    fn a_missing_directory_has_no_launchers_and_nothing_to_remove() {
        let dir = tempfile::tempdir().unwrap();
        let gone = dir.path().join("missing");
        let any = plan(Platform::Linux, "art-1", "x");

        assert!(find(&gone, &any).is_empty());
        assert!(!remove(&gone, &any).unwrap());
    }
}
