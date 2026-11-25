# web_fetch/

## Overview

Layered single-page web fetch engine with L1 HTTP fetching, optional L4 remote escalation, and full-content delivery that shortens long pages to one shared budget while saving the complete text for `file_read_tool`.

## Submodule Index

| Submodule | Role | Description |
|-----------|------|-------------|
| [engine/](engine/_ARCH.md) | Core | FetchEngine tiered fetcher pool |
| [processing/](processing/_ARCH.md) | Core | Post-fetch pipeline, markdown, charset detection, full-content delivery, anti-bot |
| [extractors/](extractors/_ARCH.md) | Core | WeChat / Bilibili / YouTube / X fast-paths |
| [fetchers/](fetchers/_ARCH.md) | Core | L1 HTTP fetcher implementation and outbound target guard |
| [router/](router/_ARCH.md) | Core | Per-domain learning metrics and site experience |
| [escalation/](escalation/_ARCH.md) | Core | L4 remote fetch hook |

## Package Root Files (facade only)

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Re-exports FetchEngine and result types | ✅ |
| web_fetch_agent_tools.py | Core | LangChain web_fetch_tool factory; with both embedding and reranker configs the tool offers `fetch_and_extract` and `fetch_full_content`, otherwise it only reads pages in full (`urls`, up to 5, plus `reason`; all pages share one budget, the complete text is saved when any page is cut, and a page whose source hit the fetch size ceiling is named by number in a notice after the untrusted block); every URL passes `_url_gate.validate_urls` first and the tool fetches the canonical spelling it returns; the user's network blocklist is read from the host's current policy on every call (`network_blocklist_provider`), and a call with a non-empty list gets an engine of its own, so a list edited mid-session applies to the next call and no page cached under another list is served; the pages of a full read are labelled with the run's source numbers (`source_indexer_var`), so each label is the index of its source card | ✅ |
| _web_fetch_tool_description.py | Core | LLM-visible tool description SSOT (EN/ZH); names the readable document formats (PDF, DOCX, XLSX, PPTX) and never mentions browser tools | ✅ |
| _url_gate.py | Core | `validate_urls` — the gate every URL of a `web_fetch_tool` call passes before any request: unwraps unambiguous model wrappers (Markdown links, matching quotes/angle brackets/backticks, unbalanced trailing parentheses without altering legal RFC 3986 characters), enforces `http`/`https` canonical spelling (`utils/url_syntax.py`, rejecting `ws`/`wss` and malformed URLs), and runs the benchmark hostname blocklist and cloud SSRF + secret-in-URL checks on the canonical host | ✅ |

## L4 Escalation (WFEL)

After local L1 HTTP failure, `FetchEngine._try_escalation` tries injected `FetchEscalationProvider`
chain (Jina then Firecrawl when enabled in server config). Providers bind per agent run via
`escalation/context.py` ContextVar — **not** global singleton mutation.

## Tool routing (web_fetch vs browser)

When both tools are mounted: read-only pages → `web_fetch_tool` (CORE); interactive flows → browser tools (EXTENDED). Guidance lives in the Dynamic Hint on `browser_navigate_tool` (`browser/tools/navigate.py`), not System Prompt; the Schema Weaver drops it when `web_fetch_tool` is not mounted, and the `web_fetch_tool` description never mentions browser tools, so it stays correct when none is mounted. Loop Guard provides symmetric fallback suggestions. See `toolkits/browser/BROWSER_SYSTEM.md` §web_fetch 与 browser 分工.

## Key Dependencies

- `toolkits.retriever` (for fetch_and_extract mode)
- `httpx`
- `[web]` optional: `curl_cffi` (L1 bounded download), `youtube-transcript-api`
