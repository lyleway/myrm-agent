# scripts/ci/ 模块架构

CI 门禁脚本与 baseline 数据。由 GitHub Actions 与 `run_architecture_gates.sh` 调用。

## 文件清单

| 文件 | 地位 | 职责 |
|------|------|------|
| `check_profile_capability_escalation.py` | 核心 | 审计服务器实际创建的内置安全配置（导入 `profile_manager._BUILTIN_PROFILES` 求值而不是解析源码：Read Only 的权限来自函数调用，字面量解析读不了，一读就抛异常）：Read Only 必须拒绝全部写文件、执行代码与浏览器改动类动作，Workspace 不得直接放行 shell / 代码 / MCP，两者都不得开启 YOLO，每个配置必须显式列出 capabilities，没有可审计的配置同样失败；阻断 PR 越权与提权放宽。`tests/architecture/test_profile_capability_gate.py` 在架构套件里独立跑同一审计，并用变异配置证明它会失败 |
| `check_pyright_unbound.py` | 核心 | 解析 pyright JSON，阻断 `reportUnboundVariable` / `reportUndefinedVariable`（“变量先用后赋值”类 bug） |
| `install_promtool.sh` | 核心 | 安装固定版本（含 sha256 校验）的 `promtool` 到 `/usr/local/bin`；架构门禁与默认 pytest 两个 workflow 共用，因为 Prometheus 规则测试在 CI 缺少 promtool 时直接失败 |
| `lib_harness_deps.sh` | 核心 | 共享 harness 感知 `uv sync`（本地树 / PyPI）：解析同仓的 `myrm-agent-harness/` 目录并 `uv sync --frozen --group dev`（harness 由 `uv.lock` 作为 editable path source 提供） |
| `run_architecture_gates.sh` | 核心 | 串联各门禁，模块可达性门禁最先运行：它的结论只覆盖本次推送区间且不会被重判（下一次推送的基线已含本次遗留的未接入模块），整树门禁则每次全量重评，所以依赖安装或整树门禁失败不能让它被跳过；它只需 git 与纯标准库的检查脚本。harness `check_module_reachability.py --base` 跑两次：harness 树（server 树作为消费者）与 `--server` server 树（`channels/providers/`、`startup/` 因按名称加载/由树外入口导入而作为根），均为 diff 模式，仅工作树（含未跟踪文件）里已死、基线时不死的单元失败，两侧输入都取自基线，两次都会执行、一侧的结论不遮住另一侧；基线修订取 `MYRM_REACHABILITY_BASE`——CI 中 PR 为 `origin/<目标分支>`、push 为推送前的提交，本地默认 `origin/main`，GitHub Actions 里未设置则报错退出（push 时 `origin/main` 就是 `HEAD`，差分恒为空）。其后依次为依赖同步（`lib_harness_deps.sh`）、fractal docs、md-refs 反引号引用校验（`validate_arch_inventory.py --md-refs`，本地 harness 源码模式生效）、导入可解析性门禁（harness `check_import_resolvability.py`：`app/` 内每条 `app.*` / `myrm_agent_harness.*` import 须指向真实模块与名字；harness 源码在旁时生效，PyPI 模式显式跳过）、profile capability 门禁、依赖漏洞门禁（工作区检查脚本在旁时运行，独立检出时跳过）、ruff lint、pyright unbound/undefined 门禁（`check_pyright_unbound.py`；CI 默认开启，本地设 `MYRM_PYRIGHT_GATE=1` 运行）、`promtool check rules`、architecture pytest |
| `run_default_tests.sh` | 核心 | ruff lint + 默认 pytest 套件（串行 `-n0`；标记过滤、超时与导入模式取自 `pyproject.toml` 的 `addopts`；写出 junit 报告 `/tmp/server-unit.xml`，CI `server-unit-tests.yml` 失败时读它把失败用例列进注解） |
| `run_locked_use_tests.sh` | 核心 | Locked Use / Privacy Curtain 聚焦 pytest（`tests/services/locked_use/` + `test_desktop_locked_use_diagnostic.py`；CI job `locked-use-pytest`） |
| `run_startup_gate_tests.sh` | 核心 | 启动导入门禁（`tests/startup/test_import_budget.py`：导入 HTTP 应用不联网、模块数不超预算）；独立于默认套件单独运行，使默认套件失败或超时时门禁结果仍可见（CI job `startup-import-gate`） |
| `file_line_budget_baseline.txt` | 数据 | 已超过 400 行的 Python 模块路径台账（相对 server 根；由 `check_file_line_budget.py --write-baseline` 维护，非 CI 阻断项） |

## 运行

```bash
bash scripts/ci/run_architecture_gates.sh
bash scripts/ci/run_default_tests.sh
```
