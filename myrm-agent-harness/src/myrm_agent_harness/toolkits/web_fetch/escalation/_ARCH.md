# escalation/

## Overview

L4 remote fetch extension point for `FetchEngine`. Framework defines Protocol, DTOs and
the ContextVar binding only — httpx vendor implementations live in
`myrm-agent-server/app/services/web_fetch/providers/`.

## Files

| File | Role | I/O/P |
|------|------|-------|
| protocols.py | `FetchEscalationProvider` Protocol + `EscalationFetchResult` | ✅ |
| context.py | Per-run ContextVar bind (`bind_web_fetch_escalation_context`) | ✅ |
| content_validator.py | `is_valid_escalation_content` central L4 response quality gate | ✅ |
| __init__.py | Public re-exports | ✅ |

## Usage

Server binds providers per agent stream; `FetchEngine._try_escalation` reads
`get_bound_escalation_providers()` after the local HTTP read fails — the bound providers are the only source, an engine holds
none of its own. Every escalation ends in one log line: the provider that succeeded, or "no usable content" with the
number of providers tried.
