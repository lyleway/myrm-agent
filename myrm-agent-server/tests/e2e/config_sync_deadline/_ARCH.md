# config_sync_deadline/

## Overview

Chrome MCP E2E helpers for the config-sync deadline live flow: edits typed into the Remote Web Fetch Fallback card while an in-page fetch shim delays or withholds the answers of its writes. The entry test lives in `../test_config_sync_deadline_chrome_e2e.py`; this package holds the shim and the card session it composes.

## File Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| `__init__.py` | Package | Docstring-only package marker | ✅ |
| `fault_shim.py` | Core | In-page `fetch` shim for `/api/v1/config`: arms one fault for the next matching request (headers slow or withheld, body slow or stalled); the real request is detached from the app's abort and still reaches the backend unless `apply` is false; logs when each request was answered and when the app cancelled it, with the page clock, so quiet time is judged on the page's own clock and page moments convert to the epoch milliseconds the backend stamps on its writes | ✅ |
| `card_session.py` | Core | Session of the Remote Web Fetch Fallback card in `/settings/search`: types the Jina key like a user (two chained edits run inside the page), reads back the shown and stored values, when and how often the backend applied a value (audit log; a version starts with its epoch millisecond) and the page's config requests; `settled_writes` returns once every write has ended and the page clock has been quiet; `run_marker` gives run-unique values so rows of earlier runs on a reused runtime never count | ✅ |

## Entry Test

`../test_config_sync_deadline_chrome_e2e.py` — Lane-B (PRIVATE + GLOBAL_WRITE) with the real frontend, backend and database and no model. Nine phases: eight on one page (healthy write, slow headers, slow body, stalled headers, stalled body, edit during a stall, stalled settlement read, healthy again) and one on a reopened page. Every timing assertion rests on page-clock moments from the shim and on the stamp the backend puts on each applied write, so a loaded host delays the verdict without changing it.

## Dependencies

- Inside the package: `card_session` ← `fault_shim`
- `tests/support/chrome_mcp_e2e/` — Chrome MCP client and page helpers (`http_json`, `wait_for_state`, `dismiss_blocking_modals`, the warm-UI wait scale)
- `myrm-agent/scripts/dev/lib/` — `cdp_chat/support/` (config value fetch), `e2e_core/orchestrator` (wall-clock progress)
