//! 系统入口（启动器）长什么样：各平台要写哪些文件、内容是什么。纯数据，不碰磁盘。
//!
//! [INPUT]
//! - 产物 ID（已按字符集校验）与应用名
//!
//! [OUTPUT]
//! - Platform: macOS / Windows / Linux
//! - Plan: 启动器入口的扩展名、标记所在文件、标记、要写的文件
//! - build_plan: 某平台上某应用的启动器
//! - launcher_url: 启动器打开的链接 `myrmagent://app/<id>`
//! - display_name / file_stem: 应用名清理后用作显示与文件名
//! - contains_marker: 文本里是否有整行等于标记的行
//!
//! [POS]
//! 启动器本身不含任何逻辑，只负责打开 `myrmagent://app/<id>`，由主应用收到链接后开窗口。
//! 「这个入口是哪个应用的」写在启动器内容里（标记行），不靠文件名：同名应用并存时文件名会带短 ID，
//! 改名后要能找到并替换旧入口。标记必须是整行，否则 `a` 会被当成 `ab` 的一部分。
//! 应用名是页面作者可控的文字，要进入 plist / `.desktop` / 文件名：控制字符一律去掉，XML 做转义，
//! 文件名里的路径字符与 Windows 保留名一并处理。

pub const URL_SCHEME: &str = "myrmagent";
const MAX_NAME_CHARS: usize = 60;
const FALLBACK_NAME: &str = "MyrmAgent";
const WINDOWS_RESERVED: [&str; 22] = [
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
    "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Platform {
    MacOs,
    Windows,
    Linux,
}

impl Platform {
    pub fn current() -> Platform {
        if cfg!(target_os = "macos") {
            Platform::MacOs
        } else if cfg!(windows) {
            Platform::Windows
        } else {
            Platform::Linux
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanFile {
    /// 入口内的相对路径，`/` 分隔；空串表示入口本身就是这个文件。
    pub relative: String,
    pub contents: Vec<u8>,
    pub executable: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub extension: &'static str,
    /// 放标记的文件（入口内的相对路径，空串为入口本身）。
    pub marker_file: &'static str,
    pub marker: String,
    pub files: Vec<PlanFile>,
}

pub fn launcher_url(artifact_id: &str) -> String {
    format!("{URL_SCHEME}://app/{artifact_id}")
}

pub fn display_name(raw: &str) -> String {
    let cleaned: String = raw
        .chars()
        .filter(|c| !c.is_control())
        .take(MAX_NAME_CHARS)
        .collect();
    let trimmed = cleaned.trim();
    if trimmed.is_empty() {
        FALLBACK_NAME.to_string()
    } else {
        trimmed.to_string()
    }
}

/// 入口文件名的主干：路径字符换成空格、折叠空白、去掉首尾的点与空格、避开 Windows 保留名。
pub fn file_stem(raw: &str) -> String {
    let replaced: String = display_name(raw)
        .chars()
        .map(|c| if "/\\:*?\"<>|".contains(c) { ' ' } else { c })
        .collect();
    let collapsed = replaced.split_whitespace().collect::<Vec<_>>().join(" ");
    let stem = collapsed.trim_matches(|c: char| c == '.' || c == ' ');
    if stem.is_empty() {
        return FALLBACK_NAME.to_string();
    }
    let head = stem.split('.').next().unwrap_or(stem);
    if WINDOWS_RESERVED
        .iter()
        .any(|r| r.eq_ignore_ascii_case(head))
    {
        return format!("Myrm {stem}");
    }
    stem.to_string()
}

pub fn contains_marker(text: &str, marker: &str) -> bool {
    text.lines().any(|line| line.trim() == marker)
}

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

pub fn build_plan(
    platform: Platform,
    artifact_id: &str,
    name: &str,
    icon_icns: Option<&[u8]>,
) -> Plan {
    let url = launcher_url(artifact_id);
    let name = display_name(name);
    match platform {
        Platform::MacOs => mac_plan(artifact_id, &url, &name, icon_icns),
        Platform::Windows => {
            let marker = format!("URL={url}");
            Plan {
                extension: ".url",
                marker_file: "",
                files: vec![PlanFile {
                    relative: String::new(),
                    contents: format!("[InternetShortcut]\r\n{marker}\r\n").into_bytes(),
                    executable: false,
                }],
                marker,
            }
        }
        Platform::Linux => {
            let marker = format!("Exec=xdg-open {url}");
            let body = format!(
                "[Desktop Entry]\nType=Application\nName={name}\n{marker}\nTerminal=false\nCategories=Utility;\n"
            );
            Plan {
                extension: ".desktop",
                marker_file: "",
                files: vec![PlanFile {
                    relative: String::new(),
                    contents: body.into_bytes(),
                    executable: true,
                }],
                marker,
            }
        }
    }
}

fn mac_plan(artifact_id: &str, url: &str, name: &str, icon_icns: Option<&[u8]>) -> Plan {
    let bundle_id = format!("com.myrm.app.{}", artifact_id.replace('_', "-"));
    let marker = format!("<string>{bundle_id}</string>");
    let escaped = xml_escape(name);
    let icon_entry = if icon_icns.is_some() {
        "\t<key>CFBundleIconFile</key>\n\t<string>icon</string>\n"
    } else {
        ""
    };
    // LSUIElement：启动器只负责转交链接，运行的瞬间不在 Dock 里跳动。
    let plist = format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
<plist version=\"1.0\">\n<dict>\n\
\t<key>CFBundleIdentifier</key>\n\t{marker}\n\
\t<key>CFBundleName</key>\n\t<string>{escaped}</string>\n\
\t<key>CFBundleDisplayName</key>\n\t<string>{escaped}</string>\n\
\t<key>CFBundleExecutable</key>\n\t<string>launcher</string>\n\
\t<key>CFBundlePackageType</key>\n\t<string>APPL</string>\n\
\t<key>CFBundleVersion</key>\n\t<string>1</string>\n\
\t<key>LSUIElement</key>\n\t<true/>\n\
{icon_entry}</dict>\n</plist>\n"
    );
    let mut files = vec![
        PlanFile {
            relative: "Contents/Info.plist".to_string(),
            contents: plist.into_bytes(),
            executable: false,
        },
        PlanFile {
            relative: "Contents/MacOS/launcher".to_string(),
            contents: format!("#!/bin/sh\nexec /usr/bin/open \"{url}\"\n").into_bytes(),
            executable: true,
        },
    ];
    if let Some(icon) = icon_icns {
        files.push(PlanFile {
            relative: "Contents/Resources/icon.icns".to_string(),
            contents: icon.to_vec(),
            executable: false,
        });
    }
    Plan {
        extension: ".app",
        marker_file: "Contents/Info.plist",
        marker,
        files,
    }
}

#[cfg(test)]
mod tests {
    use super::{build_plan, contains_marker, display_name, file_stem, launcher_url, Platform};

    fn text(plan: &super::Plan, relative: &str) -> String {
        let file = plan.files.iter().find(|f| f.relative == relative).unwrap();
        String::from_utf8(file.contents.clone()).unwrap()
    }

    #[test]
    fn the_launcher_opens_the_deep_link_of_its_own_app() {
        assert_eq!(launcher_url("art-1"), "myrmagent://app/art-1");

        let mac = build_plan(Platform::MacOs, "art-1", "Weather", None);
        let script = text(&mac, "Contents/MacOS/launcher");
        assert!(script.contains("exec /usr/bin/open \"myrmagent://app/art-1\""));
        assert!(
            mac.files
                .iter()
                .find(|f| f.relative == "Contents/MacOS/launcher")
                .unwrap()
                .executable
        );

        let windows = build_plan(Platform::Windows, "art-1", "Weather", None);
        assert!(text(&windows, "").contains("URL=myrmagent://app/art-1\r\n"));

        let linux = build_plan(Platform::Linux, "art-1", "Weather", None);
        let desktop = text(&linux, "");
        assert!(desktop.contains("Name=Weather\n"));
        assert!(desktop.contains("Exec=xdg-open myrmagent://app/art-1\n"));
    }

    #[test]
    fn the_marker_is_found_in_the_file_the_plan_says_holds_it() {
        for platform in [Platform::MacOs, Platform::Windows, Platform::Linux] {
            let plan = build_plan(platform, "art_1", "Weather", None);
            assert!(
                contains_marker(&text(&plan, plan.marker_file), &plan.marker),
                "{platform:?}"
            );
        }
    }

    #[test]
    fn one_app_id_is_never_taken_for_another_that_starts_with_it() {
        for platform in [Platform::MacOs, Platform::Windows, Platform::Linux] {
            let short = build_plan(platform, "a", "x", None);
            let long = build_plan(platform, "ab", "x", None);
            assert!(
                !contains_marker(&text(&long, long.marker_file), &short.marker),
                "{platform:?}"
            );
            assert!(
                !contains_marker(&text(&short, short.marker_file), &long.marker),
                "{platform:?}"
            );
        }
    }

    #[test]
    fn an_app_name_cannot_break_out_of_the_files_it_is_written_into() {
        let name = "A&B <x> \"q\"\nExec=evil";
        let mac = build_plan(Platform::MacOs, "id", name, None);
        let plist = text(&mac, "Contents/Info.plist");
        assert!(plist.contains("A&amp;B &lt;x&gt; &quot;q&quot;Exec=evil"));
        assert!(!plist.contains("<x>"));

        let linux = build_plan(Platform::Linux, "id", name, None);
        let desktop = text(&linux, "");
        assert_eq!(
            desktop.lines().filter(|l| l.starts_with("Exec=")).count(),
            1
        );
        assert_eq!(
            desktop.lines().filter(|l| l.starts_with("Name=")).count(),
            1
        );
    }

    #[test]
    fn the_icon_is_part_of_the_bundle_only_when_there_is_one() {
        let without = build_plan(Platform::MacOs, "id", "x", None);
        assert!(!text(&without, "Contents/Info.plist").contains("CFBundleIconFile"));

        let with = build_plan(Platform::MacOs, "id", "x", Some(b"icns"));
        assert!(text(&with, "Contents/Info.plist").contains("CFBundleIconFile"));
        assert!(with
            .files
            .iter()
            .any(|f| f.relative == "Contents/Resources/icon.icns"));
    }

    #[test]
    fn names_become_safe_file_stems() {
        for (raw, stem) in [
            ("Weather", "Weather"),
            ("a/b\\c:d", "a b c d"),
            ("  ..hidden.. ", "hidden"),
            ("CON", "Myrm CON"),
            ("nul.txt", "Myrm nul.txt"),
            ("", "MyrmAgent"),
            ("///", "MyrmAgent"),
            ("🌤 天气", "🌤 天气"),
        ] {
            assert_eq!(file_stem(raw), stem, "{raw:?}");
        }
        assert_eq!(display_name(&"x".repeat(100)).chars().count(), 60);
    }
}
