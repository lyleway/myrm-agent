# engine/

## Overview

FetchEngine 执行引擎子包：L1 HTTP + 可选 L4 远程兜底抓取入口，
职责按文件拆分（主引擎 + 批量 / 缓存 / 抓取 / 升级四组 mixin + 共享类型），
`__init__.py` 聚合导出对外符号（保持 `toolkits.web_fetch.engine` 模块语义兼容）。

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | 聚合导出 base 公开符号（FetchEngine、结果类型）。 | ✅ |
| base.py | Core | FetchEngine — the HTTP fetch entry with the optional remote fallback (mixins: batch / cache / fetch / escalation). `crawl` owns the guards (the user's network blocklist, a string check that runs before the SSRF lookup; SSRF), coalescing, cache and fail-cache bookkeeping around one `_fetch_from_network` call; owns the fetches batch calls stopped waiting for until they end and cancels them on shutdown. `crawl(max_parsed_chars=…)` sets where a PDF or Office file's parsed text is cut (`None`: the router's default): the cached page and the in-flight fetch are kept per URL and cap (the key is the URL, plus a newline and the cap for a capped call, which no URL can spell), so a caller never gets text cut at another caller's length, while the fail cache and the failure reasons are per URL because a failure does not depend on the cap. The network blocklist is fixed for the life of an engine, so its caches never hold a page fetched under another list; a caller whose list can change builds an engine per call | ✅ |
| types.py | Core | `FailureKind` (`BlockKind \| Literal["not_found"]`), `CachedDocument`, result aliases, the in-flight fetch future type and its `NO_SHARED_RESULT` marker | ✅ |
| cache_mixin.py | Core | Cache hit, coalescing, and fetch error mixin. Only fresh entries (default 15-minute TTL) are served from cache; expired entries synchronously revalidate via conditional requests or refetch, and responses with `Cache-Control: no-store` are never cached. The coalescing wait is shielded (cancelling or timing out one waiter never cancels the future the others share); a leader cancelled mid-fetch resolves its future to `NO_SHARED_RESULT`, so its waiters come round again and one of them takes the fetch over instead of waiting out the coalescing timeout. Fetch error normalisation: an `SSRFSecurityError` from any tier (the SSRF policy's verdict or the user's network blocklist's) is a terminal block (logged, remembered as failed, `None`), never an unexpected error | ✅ |
| fetch_mixin.py | Core | Network fetch (platform fast-paths + L1 HTTP + failure-history feedback) mixin. `_fetch_from_network` runs a URL's platform fast-path first (YouTube transcript, Bilibili subtitle, WeChat article, X post) and L1 HTTP when it misses or the URL has none; each stage has its own crawl timeout (a URL whose fast-path misses can take twice the cap), and an error or timeout in the fast-path ends the crawl without fallback. WeChat and X log which path served the URL. Non-degradable 4xx responses record `not_found` (404/410) or `error_page` and abort without L4 escalation. A file (PDF, Office, image) is routed to a file parser before the empty-shell check, and the crawl's `max_parsed_chars` reaches that parser only (no other payload has such a cut); one no parser reads ends L1 fetch but is still offered to the L4 remote providers. Text (plain, markdown, csv, feeds) still checks protocol WAF challenge headers (`check_challenge_headers`) before being returned as it is, skipping HTML-only body heuristics. Anti-bot classification (`classify_block`) records `blocked` / `needs_javascript` / `error_page` reasons and feeds domain failure history behind the stale site-experience hint | ✅ |
| escalation_mixin.py | Core | L4 remote escalation + bilibili cookie loader mixin. The providers are the ones bound to the current run (`escalation/context.py`); an engine holds none of its own. A provider follows redirects on its own servers, so the user's network blocklist is judged on the URL it reports back; a listed host ends the fetch for every provider (terminal). An escalation that finds nothing usable logs one line with the number of providers tried | ✅ |
| batch_mixin.py | Core | Batch crawling (`crawl_many`) mixin. `crawl_many` hands its `max_parsed_chars` to every crawl, and judges every URL by how long its own fetch has run (a URL queued behind the concurrency cap is not penalised): with no page in hand yet it is awaited until its crawl timeout plus a 3 s unwind grace; once any page is in hand it is awaited 25 s. A URL the call stops waiting for is reported as failed, but its fetch keeps running under the engine's own crawl timeout (held in `_detached_fetches`), so callers coalesced on it and the page and failure caches are unaffected; a call holding a page never ends up empty and a hung URL cannot outlast the tool timeout. Fetches are cancelled only when their call is torn down or the engine shuts down, and never a second time once the crawl timeout has cancelled them (`Task.cancelling()`), which would cut resource cleanup short | ✅ |

## Key Dependencies

- `..fetchers` (L1 HTTP fetcher 实现与出站目标守卫)
- `..router` (域名学习数据 DomainMetrics 与站点经验)
- `..escalation` (L4 远程抓取 hook)
- `..processing` (HTML → Markdown 内容管道与转换线程调度)
- `..*_extractor` (YouTube / Bilibili / WeChat / X fast-path)
