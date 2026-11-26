# app/startup 模块架构

应用启动编排模块：环境加载、配置校验、健康检查、进程锁、服务器启动器各为独立子模块，由根目录 `run.py` 编排调用。本模块自己的控制台输出（`print` 与启动失败时的 `traceback.print_exc()`，见 `uvicorn_runner.py`、`health_check.py`）直接写终端、不经日志脱敏；经脱敏的是 `logging` 记录。

---

## 文件清单

| 文件 | 地位 | 职责 | I/O/P |
|------|------|------|-------|
| __init__.py | 入口 | 包初始化 | ✅ |
| env_loader.py | 核心 | 分层加载 .env（[P]/[O]/[S]）+ 浏览器路径；[T] 测试密钥不在此加载。不清理 `__pycache__`：CPython 按源码 mtime+size 校验 .pyc，每次启动重编译 `app` 约 2k 模块只会白白多花 1.4s | ✅ |
| config_check.py | 核心 | 配置迁移、预检、变更追踪 | ✅ |
| health_check.py | 核心 | 启动前资源健康检查与自动恢复 | ✅ |
| server_lock.py | 核心 | 双闸文件锁：端口锁（活跃实例 = alive **且** 监听 target_port；僵尸 run.py 由新实例接管）+ 数据目录锁（`.data.lock`，不同端口共用 data.db/qdrant 时拒绝启动；陈旧锁按 pid/boot/create_time 自动接管；存活持有者若正在优雅停机，给 10s 交接窗口再决定接管或报错） | ✅ |
| gc_policy.py | 核心 | 启动期循环 GC 策略：`defer_collection`（进程入口先暂停循环 GC，导入 ~6.7k 模块的百万级长寿对象不再被 5~6 次 full collection 反复遍历，省约 1s）→ `settle_collection`（`gc.freeze()` 冻结启动堆再 `gc.enable()`，已启用时为空操作）；`settle_collection_after(future, deadline_seconds=30)` 在后台任务全部结束或超时后 settle，保证 warmup 卡死也不会让进程永久无 GC。仅入口暂停循环 GC（`uvicorn_runner.start_with_uvicorn` 最先调用 `defer_collection`；桌面 sidecar 在导入服务端之前由 `gc_runtime_hook.py` 直接 `gc.disable()`），`settle_collection_after` 由 `server/lifespan/background.py` 调用；测试与工具导入 `app` 保持默认行为（`settle_collection` 对它们是空操作） | ✅ |
| uvicorn_runner.py | 核心 | uvicorn 单进程启动（含 WebUI 模式；远程模式的启动横幅在建议反向代理处同时说明：同机代理须透传公网 Host 或客户端地址，否则请求被判为本机、免登录）；最先调用 `defer_collection`；`uvicorn.run` 传 `log_config=None`，uvicorn 的日志器沿用 `configure_logging()` 装的根处理器（脱敏）；级别由本启动器传的 `log_level`（调试 debug、否则 warning）与 `access_log`（仅调试开启）决定——它默认的 dictConfig 会给日志器装不脱敏的私有处理器并关闭传播（`tests/startup/test_uvicorn_log_routing.py` 守卫 `app/` 与 `run.py` 内每处 `uvicorn.run`、`uvicorn.Config`，`tests/startup/test_launcher_log_redaction.py` 验证 uvicorn 错误行里的令牌确被遮蔽）；服务停止返回后走 `os._exit`，跳过可能卡死的解释器 finalize（残留线程 join），避免「端口已释放但进程不退、继续占数据目录锁」 | ✅ |
| granian_runner.py | 核心 | granian 多进程启动（仅无嵌入式数据库场景）；命令带 `--log-config granian_log_config.json`：granian 的 `_granian` 与 `granian.access` 日志器带各自的 stdout 处理器且不向上传播（2.8.7 的 `log.py` 默认配置），输出（含未处理异常的回溯）不经根处理器，由该文件把它们的两个格式器换成脱敏格式器（`tests/startup/test_launcher_log_redaction.py` 守卫：命令带这份文件，配置过的格式器遮蔽令牌，装有 granian 时再对真实 `granian.log` 验证） | ✅ |
| granian_log_config.json | 配置 | granian 的日志配置覆盖：只含 `formatters`，把默认的 `generic`（`[级别] 消息`）与 `access`（消息）换成 harness 的 `RedactingFormatter`，格式串与 granian 默认一致；granian 以配置的顶层键整体覆盖默认配置，处理器与日志器保持原样，监督进程与每个 worker 进程各按它配置一次；格式保持 granian 原样的文本行，`MYRM_LOG_FORMAT=json` 只影响应用自己的日志；桌面 sidecar 不随包（登记在 `SERVER_DATA_NOT_SHIPPED`，安装后的应用经 `app/main.py` 启动、不运行 granian） | — |
| listen_address.py | 核心 | 桌面 sidecar（PyInstaller 入口 `app/main.py`）的监听地址：`publish_listen_address` 按运行方式解析 `(host, port)`（WebUI 取 `webui.port`、Remote 绑 `0.0.0.0`，否则取 `settings.port`），在服务器绑定前调用 `set_runtime_listen`，让 `/api/v1/health` 报告真实端口（非默认端口运行时不再沿用模块默认值 8080）；`run.py` 自行探测可用端口，直接调用 `set_runtime_listen` | ✅ |
| desktop_path.py | 辅助 | 桌面 sidecar PATH 补全（`hydrate_desktop_path`）：仅当 `MYRM_SHELL_PID` 存在（Tauri 壳注入）且非 Windows 时，用用户的 `-i -l` 登录 shell 探测 PATH（5s 超时杀进程组、输出走匿名文件、HOME 取账户目录、去除 ZDOTDIR、仅保留绝对路径），按「登录 shell → 继承 PATH → 现存常见工具目录」合并去重；常见工具目录（`COMMON_TOOL_DIRS`：`/usr/local/bin`、Homebrew、`~/.local/bin`、`~/.cargo/bin`，HOME 不可解析时只取系统目录）即使登录 shell 探测失败也保证这些 CLI 能被 bash 执行。仅处理 PATH，不导入其它变量。由 `server/lifespan/app.py` 最前置以后台任务启动，与阶段 1a（数据库/迁移）并行，阶段 1b（首个 spawn 子进程或按 PATH 检查 skill `requires.bins` 的阶段）之前必须完成（dev `run.py` 与 release PyInstaller `app/main.py` 两条入口共用 lifespan） | ✅ |
| local_search_bootstrap.py | 辅助 | 本地/WebUI 模式 best-effort SearXNG 引导（`try_start_local_search_profile`，docker compose search profile，非阻塞；供 `services/config/searxng_setup.py` 消费） | ✅ |

---

## 依赖关系

```
env_loader.py ──→ dotenv (外部)
gc_policy.py ──→ gc, asyncio (标准库)；被 uvicorn_runner（defer）与 server/lifespan/background.py（settle）消费
config_check.py ──→ app.config.* (配置层)
health_check.py ──→ app.core.infra.health (基础设施)
server_lock.py ──→ filelock, psutil (主依赖；缺包提示 uv sync)
uvicorn_runner.py ──→ gc_policy, health_check, app.config (env / deploy_mode), app.core.infra.frontend_launcher, app.services.webui (qrcode / temp_token), app.main (ASGI 应用), harness get_local_ip, uvicorn (外部)
granian_runner.py ──→ health_check, granian_log_config.json (按路径交给 granian 的 `--log-config`；其中 `RedactingFormatter` 由 granian 的 dictConfig 按点号路径导入), granian (sandbox 组：uv sync --group sandbox)
listen_address.py ──→ app.config.settings / deploy_mode, app.server.runtime_dev_info；被 app/main.py 的脚本入口消费
local_search_bootstrap.py ──→ docker compose search profile (best-effort, 非阻塞)
desktop_path.py ──→ 用户登录 shell (子进程探测，失败降级为常见工具目录)；被 app/server/lifespan/app.py 消费
run.py (根目录) ──→ 以上全部 (编排入口)
```
