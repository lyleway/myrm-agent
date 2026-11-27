# .github/actions 模块架构

## 架构概述

仓库内的本地 composite action：把多个 job 里逐字重复的步骤序列收成一处，调用方用 `uses: ./.github/actions/<名称>`（调用前必须先 `actions/checkout`，action 的代码就在仓库里）。

## 子目录

| 目录 | 职责 |
|------|------|
| `build-sidecar/` | 桌面三个平台构建 job 共用的“还原前端产物、装工具链、构建 PyInstaller sidecar”序列：下载 `frontend-tauri-build` 到 `myrm-agent-frontend/.next`，装 Bun / Rust / uv / Python 3.13，注入版本，把 `TARGET_ARCH` 导出到后续步骤，同步生产依赖 venv，运行 `sidecar/build.py`。输入 `arch`（必填：`arm64` 或 `x86_64`，构建结束时与产物架构对账）、`rust-target`（可选：macOS 构建非宿主三元组时要额外装的 Rust target）、`managed-python`（可选，默认 `false`：`'true'` 时在同步 venv 前导出 `UV_PYTHON_PREFERENCE=only-managed`，让 sidecar 用 uv 托管的 Python 冻结，而不是 runner 上 python.org 的那份；只有 macOS job 传，原因见 [sidecar/_ARCH.md](../../myrm-agent-desktop/sidecar/_ARCH.md) 的「macOS 解释器布局」） |
| `report-failed-tests/` | 测试作业失败后用的一步：读 pytest 的 junit 报告（输入 `junit`，必填，文件通配），把失败用例写进步骤摘要和注解。GitHub 每步只保留十条错误注解、匿名读者看不到作业日志，所以第一条注解列出全部失败用例（超过 6 万字符时以“还有 N 个”收尾，不截断用例名），其后九条各带一条用例的消息；报告文件缺失或读不出时写“没有 junit 条目”，说明测试没有跑完。脚本 `report_failed_tests.py` 恒以 0 退出，作业此时已是红的。harness 的单测与浏览器作业、server 默认测试作业在测试步骤之后以 `if: failure()` 调用 |
| `install-tauri-cli/` | 三个平台构建 job 在 `tauri-action` 之前装的 Tauri CLI，版本在 `action.yml` 里写死（现为 `2.12.4`）。签名与打包的 bundler 在这个 CLI 里；`tauri-action` 不带 `tauriScript` 时会自己装浮动的 `@tauri-apps/cli@v2`，bundler 的行为就随 2.x 的新发布而变；所以各 job 以 `tauriScript: tauri` 调用这份钉死的版本，它只在改本文件时变 |

## 约束

- composite 里的 `run` 步骤必须写 `shell: bash`（Windows 的默认 shell 是 pwsh，而这些脚本是 bash）；`RELEASE_TAG` 来自调用 workflow 的 `env`
- 第三方 action 的钉 SHA 规则与 workflows 相同（见 [../workflows/_ARCH.md](../workflows/_ARCH.md)）
- 调用方与本 action 的约定由 `myrm-agent-server/tests/architecture/test_desktop_release_inputs.py` 钉住：每个构建 job 恰好调用一次且传入各自平台的架构、macOS 的 `rust-target` 与 Tauri 构建的 `--target` 一致、sidecar 只在这里构建、`TARGET_ARCH` 导出与 venv 同步先于 `build.py`；只有 macOS job 传 `managed-python: 'true'`，导出托管 Python 偏好的步骤带条件且先于 venv 同步，由 `test_desktop_sidecar_python.py` 钉住；`report-failed-tests` 的约定由 `myrm-agent-server/tests/architecture/test_report_failed_tests_action.py` 钉住：脚本读什么、打印什么（全集在前、至多十条注解、工作流命令字符转义）、每个写 junit 报告的作业在测试之后以 `if: failure()` 且同一路径调用它、server 作业读它的运行脚本写出的路径、action 以输入运行脚本；`install-tauri-cli` 的约定由 `test_desktop_macos_signing.py` 钉住：每个构建 job 都在 `tauri-action` 之前装它、`tauri-action` 带 `tauriScript: tauri`、版本是精确的 x.y.z 且各平台一致

## 依赖

- [scripts/ci/desktop-release/_ARCH.md](../../scripts/ci/desktop-release/_ARCH.md) — `inject-version.sh`、`sync-server-venv.sh`
- [myrm-agent-desktop/sidecar/_ARCH.md](../../myrm-agent-desktop/sidecar/_ARCH.md) — `build.py`
- 父模块 [../_ARCH.md](../_ARCH.md)
