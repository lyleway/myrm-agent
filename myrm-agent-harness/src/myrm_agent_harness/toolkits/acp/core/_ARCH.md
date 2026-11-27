# core/

## Overview
Shared runtime infrastructure for the ACP toolkit — cross-cutting mechanisms consumed by the runtime and server subsystems.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Package marker with module overview; no public re-exports. | ✅ |
| backend_detector.py | Core | Automatic detection of CLI agent backends (process-level dual cache + soft TTL + optional `refresh` bypass); versions and login states are asked concurrently, each through `probe.py`. | ✅ |
| probe.py | Core | `run_probe`: ask a CLI a short question (`--version`, `auth status`) with an exact environment, bounded in time and output, stdin closed, the whole process tree killed at the deadline. | ✅ |

## Module Dependencies

- `toolkits/acp/types.py` — runtime type definitions (contract layer)
- `toolkits/acp/auth/` — backend detector only (`cli_base_env` for the probe environment; `CredentialState` under TYPE_CHECKING)
- `toolkits/acp/toolchains/` — backend detector only (toolchain base dir)
