# fetchers/

## Overview
Toolkits Web_Fetch Fetchers module.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package |   Init   | — |
| bounded_get.py | Core | `bounded_get` — one GET through Scrapling whose body is held to `MAX_TRANSFER_BYTES` (the binary router's `MAX_BINARY_SIZE`, 20 MB) while it is received. Scrapling buffers and parses a whole response before any size check can run, so a compressed body that decodes to gigabytes, or a slow giant page, would cost memory and event-loop time in proportion to what the server sends. libcurl hands each decoded chunk to a sink that keeps the first `MAX_TRANSFER_BYTES + 1` bytes (the extra byte tells a file over the limit from one exactly at it) and ends the transfer by returning `CURL_WRITEFUNC_ERROR`; status and headers then come from the write error, which carries the response received so far. The request is attempted once; any other failure is raised. Given `addresses` (the ones the SSRF check looked at) it connects only to those, inside `pinned_connection`; a request through a proxy is not pinned, because the proxy resolves the host itself. Returns `FetchedResponse`. curl_cffi and Scrapling are imported when a request is made, so importing the engine needs neither. | ✅ |
| connection_pin.py | Core | `pinned_connection(url, addresses)` — builds libcurl `CURLOPT_RESOLVE` options so the connection goes only to the addresses the SSRF check vetted while keeping the host name for TLS SNI/certificate validation. | ✅ |
| http_fetcher.py | Core | L1 HTTP fetcher. SSRF defense through `FetchTargetGuard` on every hop (the requested URL and each redirect `Location`): a hop is judged and requested in its canonical spelling (`canonical_url`), so libcurl connects to the host the policy classified. The user's network blocklist is judged on the same hops, each before it is requested and whether or not the SSRF shield is on. Every body is received through `bounded_get` (no response holds more than `MAX_TRANSFER_BYTES + 1` bytes) and classified once by `utils.content_sniff.classify_payload`. | ✅ |
| target_guard.py | Core | `FetchTargetGuard` — per-fetch outbound policy: SSRF policy and user network blocklist (`assert_not_blocklisted`). | ✅ |
| protocols.py | Core | Provides FetcherType (`HTTP`), FetchResult (with `kind: PayloadKind`, WEB by default and BINARY whenever `raw_body` is set, and `source_truncated` when the fetcher cut the body at its size ceiling), Fetcher protocol. | ✅ |
