# config_sync_deadline/

## Overview

Chrome MCP E2E helpers for the config-sync deadline live flow: edits typed into the Remote Web Fetch Fallback card while an in-page fetch shim delays or withholds the answers of its writes, or while the backend process itself is stopped. The entry tests live in `../test_config_sync_deadline_chrome_e2e.py` and `../test_config_sync_frozen_backend_chrome_e2e.py`; this package holds the shim, the card session, the scenario scaffolding, the shared assertions and the backend freeze they compose.

## File Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| `__init__.py` | Package | Docstring-only package marker | ✅ |
| `fault_shim.py` | Core | In-page `fetch` shim for `/api/v1/config`: arms one fault for the next matching request (headers slow or withheld, body slow or stalled); the real request is detached from the app's abort and still reaches the backend unless `apply` is false; logs when each request was answered and when the app cancelled it, with the page clock, so quiet time is judged on the page's own clock and page moments convert to the epoch milliseconds the backend stamps on its writes | ✅ |
| `card_session.py` | Core | Session of the Remote Web Fetch Fallback card in `/settings/search`: types the Jina key like a user (two chained edits run inside the page), reads back the shown and stored values, when and how often the backend applied a value (audit log; a version starts with its epoch millisecond) and the page's config requests; `settled_writes` returns once every write has ended and the page clock has been quiet, `wait_for_cancelled_writes` once the app has cancelled a given number of writes; `run_marker` gives run-unique values so rows of earlier runs on a reused runtime never count | ✅ |
| `scenario.py` | Core | One scenario's page: `deadline_card` stores a config, opens the settings page with the shim installed and puts the previous config back afterwards; `run_phases` runs the named phases, gathers every failed assertion together with the page's request log and prints one `E2E_CONFIG_SYNC_DEADLINE` evidence line | ✅ |
| `assertions.py` | Core | The two deadlines under test, the timing tolerances and what every scenario shares: page-moment helpers that fail with a message when the request never reached the point measured, and `one_answered_write` (one edit, one answered write, one audit-log row) | ✅ |
| `backend_freeze.py` | Core | Stops the isolated backend with SIGSTOP so the browser meets a backend that takes a request and never answers, and always resumes it: on leaving the block, and by a detached watchdog that resumes it after a time limit even if the test process died; refuses any process that is not the only listener of the API port and owned by this runtime | ✅ |

## Entry Tests

Both are Lane-B (PRIVATE) with the real frontend, backend and database and no model. Each node is one scenario on a page of its own, so a failure names its scenario and a run stays inside the protocol's body time.

- `../test_config_sync_deadline_chrome_e2e.py` (`GLOBAL_WRITE`) — faults armed in the page's fetch shim: answers inside the deadlines are accepted and written once; an answer that never starts and a body that never ends are cancelled at their deadlines and the edit lands once; an edit made during a stall replaces the edit whose answer was lost; a stalled conflict-settlement read is cancelled and the edit lands once; a fresh page shows the stored config and its first edit lands once.
- `../test_config_sync_frozen_backend_chrome_e2e.py` (`GLOBAL_WRITE`, `fault_injection`) — the backend process is really stopped: every attempt is cancelled at the headers deadline, the retries back off 1 s and then 2 s, and the edit lands once after the backend runs again.

Every timing assertion rests on page-clock moments from the shim and on the stamp the backend puts on each applied write, so a loaded host delays the verdict without changing it.

## Unit Tests

`../../unit/config_sync_deadline/` covers every module without a browser: the Python halves with a faked page, backend and process table (`test_fault_shim.py`, `test_card_session.py`, `test_card_session_cancelled_wait.py`, `test_scenario.py`, `test_assertions.py`), the freeze against real child processes and a real listener (`test_backend_freeze.py`, which needs `lsof`), and the shim's JavaScript run in Node against a stub `fetch` (`test_fault_shim_script.py`, which needs `node` on `PATH`). Run them with `./myrm-dev test -n0 --cov=tests.e2e.config_sync_deadline --cov-branch --cov-report=term-missing myrm-agent/myrm-agent-server/tests/unit/config_sync_deadline`.

## Dependencies

- Inside the package: `card_session` ← `fault_shim`; `scenario` ← `card_session`, `fault_shim`; `assertions` ← `card_session`, `fault_shim`
- `tests/support/chrome_mcp_e2e/` — Chrome MCP client and page helpers (`http_json`, `wait_for_state`, `dismiss_blocking_modals`, `open_settings_subroute`, `warm_ui_route`, the warm-UI wait scale)
- `myrm-agent/scripts/dev/lib/` — `cdp_chat/support/` (config value fetch and write, the E2E API URL, provider readiness), `e2e_core/orchestrator` (wall-clock progress), `e2e_core/dev_ownership` (proof that a process belongs to this runtime)
