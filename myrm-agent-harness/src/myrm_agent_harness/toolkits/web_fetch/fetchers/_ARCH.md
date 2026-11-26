# fetchers/

## Overview
Toolkits Web_Fetch Fetchers module.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package |   Init   | — |
| bounded_get.py | Core | `bounded_get` and typed `RequestParams` — direct curl_cffi `AsyncSession.get` (Chrome impersonation, default Referer) whose body is held to `MAX_TRANSFER_BYTES` (20 MB) via libcurl `content_callback` + `CURL_WRITEFUNC_ERROR`, with session-level `CURLOPT_RESOLVE` connection pinning via `build_pin_curl_options`. | ✅ |
| connection_pin.py | Core | `build_pin_curl_options(url, addresses)` — builds libcurl `CURLOPT_RESOLVE` options so the direct curl_cffi session connects only to vetted addresses while keeping the host name for TLS SNI/certificate validation. | ✅ |
| http_fetcher.py | Core | L1 HTTP fetcher. Sends `Accept: text/markdown, text/html;q=0.9, */*;q=0.1` by default (overridden by WeChat request headers for `mp.weixin.qq.com`) so Markdown-capable origins return Markdown directly without HTML parsing. SSRF defense through `FetchTargetGuard` on every hop (the requested URL and each redirect `Location`): a hop is judged and requested in its canonical spelling (`canonical_url`), so libcurl connects to the host the policy classified. The user's network blocklist is judged on the same hops, each before it is requested and whether or not the SSRF shield is on. Every body is received through `bounded_get` (no response holds more than `MAX_TRANSFER_BYTES + 1` bytes) and classified once by `utils.content_sniff.classify_payload`. | ✅ |
| target_guard.py | Core | `FetchTargetGuard` — per-fetch outbound policy: SSRF policy and user network blocklist (`assert_not_blocklisted`). | ✅ |
| protocols.py | Core | Provides FetcherType (`HTTP`), FetchResult (with `kind: PayloadKind`, WEB by default and BINARY whenever `raw_body` is set, and `source_truncated` when the fetcher cut the body at its size ceiling), Fetcher protocol. | ✅ |
