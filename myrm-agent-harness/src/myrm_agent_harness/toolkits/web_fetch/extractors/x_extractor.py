"""Twitter / X post fast-path extractor.

Extracts post content, author metadata, engagement metrics, and media links
via lightweight public APIs (FxTwitter / VxTwitter / oEmbed).
Used as a fast-path shortcut in FetchEngine when an X or Twitter URL is detected,
bypassing the HTML fetch to deliver structured Markdown Documents.

When the fast-path is unavailable or returns 404, FetchEngine degrades gracefully.

[INPUT]
- x.com or twitter.com post URL

[OUTPUT]
- is_x_url: Detect x.com / twitter.com status URLs; the host must start the URL, a status link inside another site's URL is no post
- extract_x_post: Fetch + parse post into Document, or None
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.request
from typing import TYPE_CHECKING

from langchain_core.documents import Document

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.browser.pool.proxy import ProxyPool

logger = logging.getLogger(__name__)

# The host starts the URL (matched with ``re.match``): a status path inside another site's URL (an archive copy,
# a ``notx.com`` host, a query parameter) is not an X post and must be fetched like any page.
_X_STATUS_RE = re.compile(
    r"(?:https?://)?"
    r"(?:(?:www\.|mobile\.)?(?:twitter\.com|x\.com|vxtwitter\.com|fxtwitter\.com|fixupx\.com))"
    r"/([a-zA-Z0-9_]+)/status/(\d+)",
    re.IGNORECASE,
)

_REQUEST_TIMEOUT = 8
_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

_JsonObject = dict[str, object]


def _as_object(value: object) -> _JsonObject:
    """The value when it is a JSON object, an empty one otherwise: the API's shape is not ours to trust."""
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_count(value: object) -> int:
    return value if isinstance(value, int) else 0


def is_x_url(url: str) -> bool:
    """Return True when url is a valid Twitter/X post status link."""
    if not url:
        return False
    return _X_STATUS_RE.match(url.strip()) is not None


def extract_status_info(url: str) -> tuple[str, str] | None:
    """Extract (screen_name, status_id) from an X/Twitter URL."""
    match = _X_STATUS_RE.match(url.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def _build_opener(proxy_pool: ProxyPool | None = None) -> urllib.request.OpenerDirector:
    """Build urllib opener with optional proxy support."""
    if proxy_pool:
        proxy_config = proxy_pool.get_next()
        proxy_url = proxy_config.to_url()
        handler = urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})
        return urllib.request.build_opener(handler)
    return urllib.request.build_opener()


def _fetch_fxtwitter_data(
    screen_name: str,
    status_id: str,
    opener: urllib.request.OpenerDirector,
) -> _JsonObject | None:
    """Fetch structured post data via api.fxtwitter.com."""
    endpoint = f"https://api.fxtwitter.com/{screen_name}/status/{status_id}"
    req = urllib.request.Request(
        endpoint,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    try:
        with opener.open(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = _as_object(json.loads(resp.read().decode("utf-8")))
            tweet = _as_object(data.get("tweet"))
            if data.get("code") == 200 and tweet:
                return tweet
    except Exception as exc:
        logger.debug("FxTwitter API fetch failed for %s/%s: %s", screen_name, status_id, exc)
    return None


def _fetch_oembed_data(
    url: str,
    opener: urllib.request.OpenerDirector,
) -> _JsonObject | None:
    """Fallback: Fetch oEmbed metadata via publish.twitter.com."""
    endpoint = f"https://publish.twitter.com/oembed?url={url}&omit_script=true"
    req = urllib.request.Request(
        endpoint,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    try:
        with opener.open(req, timeout=_REQUEST_TIMEOUT) as resp:
            return _as_object(json.loads(resp.read().decode("utf-8"))) or None
    except Exception as exc:
        logger.debug("Twitter oEmbed fetch failed for %s: %s", url, exc)
    return None


def _format_tweet_document(tweet: _JsonObject, original_url: str) -> Document:
    """Convert FxTwitter tweet dict to structured Markdown Document."""
    author = _as_object(tweet.get("author"))
    author_name = _as_text(author.get("name"))
    screen_name = _as_text(author.get("screen_name"))
    text = _as_text(tweet.get("text")).strip()
    created_at = _as_text(tweet.get("created_at"))
    likes = _as_count(tweet.get("likes"))
    retweets = _as_count(tweet.get("retweets"))
    replies = _as_count(tweet.get("replies"))

    header = f"**{author_name}** (@{screen_name})" if screen_name else f"**{author_name}**"
    lines: list[str] = [header]
    if created_at:
        lines.append(f"*发布时间*: {created_at}")
    lines.append("")
    lines.append(text)

    # Media attachments (photos / videos)
    media = _as_object(tweet.get("media"))
    media_urls: list[str] = []
    for photo in _as_list(media.get("photos")):
        photo_url = _as_text(_as_object(photo).get("url"))
        if photo_url:
            media_urls.append(photo_url)
            lines.append(f"![Image]({photo_url})")
    for video in _as_list(media.get("videos")):
        video_fields = _as_object(video)
        thumbnail_url = _as_text(video_fields.get("thumbnail_url"))
        if thumbnail_url:
            media_urls.append(_as_text(video_fields.get("url")) or thumbnail_url)

    # Engagement line
    engagement_parts: list[str] = []
    if retweets:
        engagement_parts.append(f"🔁 {retweets} 转发")
    if likes:
        engagement_parts.append(f"❤️ {likes} 喜欢")
    if replies:
        engagement_parts.append(f"💬 {replies} 回复")
    if engagement_parts:
        lines.append("")
        lines.append(f"> {' · '.join(engagement_parts)}")

    page_content = "\n".join(lines).strip()

    metadata: dict[str, str | int] = {
        "url": original_url,
        "title": f"Post by @{screen_name}: {text[:60]}..." if len(text) > 60 else f"Post by @{screen_name}: {text}",
        "author": author_name,
        "screen_name": screen_name,
        "source_type": "x_post",
        "likes": likes,
        "retweets": retweets,
    }
    if created_at:
        metadata["created_at"] = created_at
    if media_urls:
        metadata["media_urls"] = json.dumps(media_urls, ensure_ascii=False)

    return Document(page_content=page_content, metadata=metadata)


def _format_oembed_document(oembed: _JsonObject, original_url: str) -> Document:
    """Convert oEmbed dict to fallback Document."""
    author_name = _as_text(oembed.get("author_name"))
    author_url = _as_text(oembed.get("author_url"))
    raw_html = _as_text(oembed.get("html"))

    # Strip HTML tags simply
    text_content = re.sub(r"<[^>]+>", " ", raw_html).strip()
    text_content = re.sub(r"\s+", " ", text_content)

    lines: list[str] = []
    if author_name:
        lines.append(f"**{author_name}** ({author_url})")
        lines.append("")
    lines.append(text_content)

    return Document(
        page_content="\n".join(lines),
        metadata={
            "url": original_url,
            "title": f"Post by {author_name}",
            "author": author_name,
            "source_type": "x_post_oembed",
        },
    )


async def extract_x_post(
    url: str,
    *,
    proxy_pool: ProxyPool | None = None,
) -> Document | None:
    """Extract Twitter / X post and return as Document, or None on failure.

    Args:
        url: Status URL (x.com/user/status/123 or twitter.com/...)
        proxy_pool: Optional proxy pool for network egress.

    Returns:
        Document with structured post markdown and metadata, or None.
    """
    if not is_x_url(url):
        return None

    status_info = extract_status_info(url)
    if not status_info:
        return None

    screen_name, status_id = status_info
    opener = _build_opener(proxy_pool)

    def _do_fetch() -> Document | None:
        # FxTwitter API: rich JSON
        tweet = _fetch_fxtwitter_data(screen_name, status_id, opener)
        if tweet:
            return _format_tweet_document(tweet, url)

        # Twitter's own oEmbed: the HTML embed as a fallback
        oembed = _fetch_oembed_data(url, opener)
        if oembed:
            return _format_oembed_document(oembed, url)

        return None

    return await asyncio.to_thread(_do_fetch)
