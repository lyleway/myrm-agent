# toolchains/

## Overview
Isolated portable toolchain manager for external CLI agents (Node.js download, npm CLI install) without polluting the host OS environment.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Re-exports TOOLCHAIN_DIR_ENV, IsolatedToolchainManager, ToolchainError, toolchain_base_dir and toolchain_node_bin | — |
| manager.py | Core | IsolatedToolchainManager — download the portable Node (`NODE_VERSION`, a line that reads `NODE_USE_SYSTEM_CA`) and install CLI binaries under `toolchain_base_dir()`: `$MYRM_TOOLCHAIN_DIR` when a host names one, else `~/.myrm-agent/toolchains`, read on every call so the installer, `toolkits/acp/core/backend_detector.py` and the launch environment always agree. An install ends in a CLI that answers `--version` or in a `ToolchainError` that says what failed (an `Error:` line in a progress stream is gone): one install at a time per directory through a kernel `flock` (a killed installer leaves no lock; a waiter gives up after 10 minutes), a download that stalls for 60 s or runs past 15 minutes stops, npm runs in its own process group with a 15-minute limit and is stopped with everything it started when the stream is closed. npm follows the user's own network setup: it gets the core environment plus `npm_config_*` variables (registry, proxy) and reads `~/.npmrc`; no registry is passed, so the default is the official one. The npm child gets `NODE_USE_SYSTEM_CA=1` through `infra/tls_compat.py::with_node_system_ca` while enterprise network compatibility is on. `toolchain_node_bin(command)` names the managed Node for a CLI this manager installed: such a CLI starts with `#!/usr/bin/env node`, so `auth/launch_env.py::cli_base_env` puts that Node first on its PATH and it runs on a host with no Node of its own | ✅ |

## Module Dependencies

- Stdlib + asyncio, `core/security/process_env` (npm environment), `core/security/bounded_communicate` (the post-install `--version`, output capped) and `utils/os_compat` (process groups); `fcntl` is imported when the lock is taken, so the package imports on Windows (an install there stops with `ToolchainError`)
- Used by `toolkits/acp/` runtime backends and `toolkits/acp/core/backend_detector.py`

## Division vs toolkits/acp/

| Package | Responsibility |
|---------|----------------|
| `toolkits/acp/` | ACP protocol server, runtime, permission, event bus |
| `toolchains/` | Isolated Node/npm environment for CLI-based ACP backends |
