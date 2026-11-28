# extractors/

## Overview

Third-party content extractors — fast-path parsing for WeChat / Bilibili / YouTube / X,
used by the FetchEngine ahead of L1 HTTP fetch and optional L4 remote escalation. Each extractor owns parsing
for a single source and may leverage optional deps (`youtube-transcript-api`) or the
SessionVault cookie store (Bilibili AI subtitles) without requiring a browser.

[INPUT]
- (none — pure parsing + fast-path helpers; `weixin_extractor` uses `..html_to_markdown`)

[OUTPUT]
- extract_weixin_article / get_weixin_request_headers / is_weixin_article_url / WEIXIN_BODY_TRUNCATED_MARKER
- extract_bilibili_subtitle / is_bilibili_url
- extract_youtube_transcript / is_youtube_url
- extract_x_post / is_x_url

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Aggregation facade — re-exports the extractors' public symbols. | ✅ |
| weixin_extractor.py | Core | WeChat Official Account fast-path — MicroMessenger UA + `#js_content` extraction (host `/s` URLs, data-src images, publish_time metadata); follows the article's one `?nwr_flag=1` bounce but never leaves https `mp.weixin.qq.com` article URLs; HTTP/L4 fallback when blocked. | ✅ |
| bilibili_extractor.py | Core | Bilibili subtitle fast-path — public API + SessionVault cookie for AI subtitles; HTTP/L4 fallback when unavailable. | ✅ |
| youtube_extractor.py | Core | YouTube transcript fast-path — `[web]` optional `youtube-transcript-api` + oEmbed metadata (title/author); HTML fallback when missing. | ✅ |
| x_extractor.py | Core | Twitter / X post fast-path — FxTwitter API / oEmbed structured extraction (text, metrics, media); HTTP/L4 fallback on failure. Only a URL whose host is X's starts the fast path: a status link inside another site's URL is fetched like any page. | ✅ |

## Key Dependencies

- `..html_to_markdown` (WeChat article HTML → Markdown)
- `[web]` optional: `youtube-transcript-api`
- SessionVault cookie (Bilibili AI subtitles)

## Consumers

- `web_fetch/engine/fetch_mixin.py` (`_fetch_from_network`) — fast-path ahead of L1 HTTP fetch and optional L4 escalation
