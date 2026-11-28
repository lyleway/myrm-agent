"""Bilibili subtitle extractor.

Extracts subtitles from Bilibili videos via public API. Used as a fast-path
shortcut in FetchEngine when a Bilibili URL is detected, bypassing the
HTML fetch.

Bilibili subtitles are loaded asynchronously via a separate API and are NOT
present in the page DOM, so fetching the page cannot retrieve them.
This extractor is the only way to obtain Bilibili video subtitles.

When the API call fails or no subtitle is available, FetchEngine falls back
to the standard HTML fetch (which provides title/description but no subtitles).

Design pattern: analogous to youtube_extractor.py — special content source routing.

[INPUT]
- (none — standalone module; uses only stdlib urllib + json)

[OUTPUT]
- is_bilibili_url: Check if a URL is a Bilibili video URL
- extract_bilibili_subtitle: Extract subtitle and return as Document, or None

[POS]
Bilibili subtitle fast-path extractor. Returns timestamped Markdown Documents
with video metadata (title, author, duration) via Bilibili public API; returns
None for HTTP/L4 fallback when subtitle unavailable.
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

_BILIBILI_URL_RE = re.compile(
    r"(?:https?://)"
    r"(?:www\.|m\.)?"
    r"bilibili\.com/video/"
    r"(BV[a-zA-Z0-9]{10}|av\d+)",
)

_BILIBILI_SHORT_URL_RE = re.compile(
    r"(?:https?://)"
    r"b23\.tv/"
    r"([a-zA-Z0-9]+)",
)

_VIEW_API = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
_PLAYER_API = "https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}"
_REQUEST_TIMEOUT = 8
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}


def is_bilibili_url(url: str) -> bool:
    """Check if a URL points to a Bilibili video."""
    return _BILIBILI_URL_RE.search(url) is not None or _BILIBILI_SHORT_URL_RE.search(url) is not None


def _extract_bvid(url: str) -> str | None:
    """Extract BV ID from a Bilibili URL."""
    match = _BILIBILI_URL_RE.search(url)
    if match:
        video_id = match.group(1)
        if video_id.startswith("BV"):
            return video_id
        return None
    return None


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS or MM:SS."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _build_opener(proxy_pool: ProxyPool | None = None) -> urllib.request.OpenerDirector:
    """Build urllib opener with optional proxy support."""
    if proxy_pool:
        proxy_config = proxy_pool.get_next()
        proxy_url = proxy_config.to_url()
        handler = urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})
        return urllib.request.build_opener(handler)
    return urllib.request.build_opener()


def _api_request(
    url: str, opener: urllib.request.OpenerDirector, cookies: dict[str, str] | None = None
) -> dict[str, object]:
    """Make an API request to Bilibili and return parsed JSON."""
    headers = dict(_DEFAULT_HEADERS)
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers["Cookie"] = cookie_str

    req = urllib.request.Request(url, headers=headers)  # noqa: S310
    with opener.open(req, timeout=_REQUEST_TIMEOUT) as resp:
        parsed = json.loads(resp.read().decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}


async def _fetch_video_metadata(
    bvid: str,
    opener: urllib.request.OpenerDirector,
) -> dict[str, str | int] | None:
    """Fetch video metadata (title, author, cid, duration) via Bilibili view API."""

    def _do_fetch() -> dict[str, str | int] | None:
        try:
            data = _api_request(_VIEW_API.format(bvid=bvid), opener)
            if data.get("code") != 0:
                logger.info("Bilibili view API returned code %s for %s", data.get("code"), bvid)
                return None
            video_data = data.get("data")
            if not isinstance(video_data, dict):
                return None
            owner = video_data.get("owner")
            author_name = str(owner.get("name", "")) if isinstance(owner, dict) else ""
            cid_raw = video_data.get("cid", 0)
            duration_raw = video_data.get("duration", 0)
            return {
                "title": str(video_data.get("title", "")),
                "author_name": author_name,
                "cid": int(cid_raw) if isinstance(cid_raw, (int, float, str)) else 0,
                "duration": int(duration_raw) if isinstance(duration_raw, (int, float, str)) else 0,
                "bvid": bvid,
            }
        except Exception as exc:
            logger.debug("Bilibili metadata fetch failed for %s: %s", bvid, exc)
            return None

    return await asyncio.to_thread(_do_fetch)


async def _fetch_subtitle(
    bvid: str,
    cid: int,
    opener: urllib.request.OpenerDirector,
    cookies: dict[str, str] | None = None,
) -> list[dict[str, float | str]] | None:
    """Fetch subtitle segments from Bilibili player API.

    Tries to get CC subtitles first (no login needed for some videos),
    then AI-generated subtitles (requires login cookie).
    """

    def _do_fetch() -> list[dict[str, float | str]] | None:
        try:
            data = _api_request(
                _PLAYER_API.format(bvid=bvid, cid=cid),
                opener,
                cookies=cookies,
            )
            if data.get("code") != 0:
                logger.debug("Bilibili player API returned code %s for %s", data.get("code"), bvid)
                return None

            raw_data = data.get("data")
            subtitle_obj = raw_data.get("subtitle") if isinstance(raw_data, dict) else None
            subtitles = subtitle_obj.get("subtitles") if isinstance(subtitle_obj, dict) else None
            if not isinstance(subtitles, list) or not subtitles:
                return None

            first_sub = subtitles[0]
            subtitle_url_raw = first_sub.get("subtitle_url", "") if isinstance(first_sub, dict) else ""
            subtitle_url = str(subtitle_url_raw) if subtitle_url_raw else ""
            if not subtitle_url:
                return None

            if subtitle_url.startswith("//"):
                subtitle_url = "https:" + subtitle_url

            subtitle_data = _api_request(subtitle_url, opener, cookies=cookies)
            body = subtitle_data.get("body")
            if not isinstance(body, list) or not body:
                return None

            segments: list[dict[str, float | str]] = []
            for item in body:
                if not isinstance(item, dict):
                    continue
                seg: dict[str, float | str] = {}
                for k, v in item.items():
                    if isinstance(v, (int, float)):
                        seg[str(k)] = float(v)
                    elif isinstance(v, str):
                        seg[str(k)] = v
                if seg:
                    segments.append(seg)
            return segments or None
        except Exception as exc:
            logger.debug("Bilibili subtitle fetch failed for %s: %s", bvid, exc)
            return None

    return await asyncio.to_thread(_do_fetch)


async def extract_bilibili_subtitle(
    url: str,
    *,
    cookies: dict[str, str] | None = None,
    proxy_pool: ProxyPool | None = None,
) -> Document | None:
    """Extract Bilibili video subtitle and return as a Document.

    Args:
        url: Bilibili video URL
        cookies: Optional bilibili.com cookies from SessionVault for AI subtitle access
        proxy_pool: Optional proxy pool

    Returns:
        Document with timestamped subtitle as page_content and video metadata,
        or None if subtitle is unavailable (falls back to HTTP/L4 fetch).
    """
    bvid = _extract_bvid(url)
    if not bvid:
        logger.debug("Failed to extract BV ID from URL: %s", url[:100])
        return None

    opener = _build_opener(proxy_pool)

    metadata_result = await _fetch_video_metadata(bvid, opener)
    if not metadata_result:
        return None

    cid = int(metadata_result["cid"])
    if cid == 0:
        return None

    segments = await _fetch_subtitle(bvid, cid, opener, cookies=cookies)
    if not segments:
        return None

    timestamped_lines = [f"{_format_timestamp(float(seg.get('from', 0)))} {seg.get('content', '')}" for seg in segments]
    full_text = "\n".join(timestamped_lines)

    duration = int(metadata_result.get("duration", 0))
    duration_str = _format_timestamp(duration) if duration > 0 else ""

    doc_metadata: dict[str, str | int | float] = {
        "url": url,
        "source_type": "bilibili_subtitle",
        "bvid": bvid,
        "segment_count": len(segments),
    }
    if metadata_result.get("title"):
        doc_metadata["title"] = str(metadata_result["title"])
    if metadata_result.get("author_name"):
        doc_metadata["author_name"] = str(metadata_result["author_name"])
    if duration_str:
        doc_metadata["duration"] = duration_str

    return Document(page_content=full_text, metadata=doc_metadata)
