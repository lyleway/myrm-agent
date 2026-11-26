"""Anti-bot and error page detection for crawl results.

Three-tier layered detection to determine if a crawl was blocked
by anti-bot protection or returned an error page.

Detection philosophy:
  false positives are cheap (the fallback/degradation rescues them),
  false negatives are catastrophic (Agent gets garbage content).
  Err on the side of detection.

Tiers:
  - Tier 1: High-confidence WAF structural markers → any page size
  - Tier 2: Medium-confidence generic patterns → short pages only
  - Tier 3: Structural integrity → catches silent blocks / empty shells / untitled script interstitials

An HTML page served with 403 or a server error (5xx) is an error or challenge page whatever its size: such pages
ship inline bundles that push them past every size gate, so the status decides, not the byte count. Other 4xx
answers (404, 410, ...) are the origin's final word on the request: trying another tier cannot change them, so the
content rules judge them.

[INPUT]
- web_fetch.processing.html_scan::body_content, extract_title, strip_script_and_style, strip_tags (POS: Linear-time scans of raw HTML text)

[OUTPUT]
- BlockKind: Failure classification literal ("blocked" | "needs_javascript" | "error_page")
- check_challenge_headers: Checks protocol-level WAF challenge response headers (Cloudflare / AWS WAF)
- classify_block: Classifies a response as explicit site block ("blocked"), JS-required shell ("needs_javascript"), generic HTTP/error page ("error_page"), or OK (None)
- is_blocked: Tells whether a crawl result was blocked by anti-bot protection or is an error page, with the reason

[POS]
Anti-bot and error page detection for crawl results.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from .html_scan import body_content, extract_title, strip_script_and_style, strip_tags

BlockKind = Literal["blocked", "needs_javascript", "error_page"]

# ---------------------------------------------------------------------------
# Tier 1: High-confidence structural markers (any page size)
# Unique to block pages; virtually never appear in real content.
# ---------------------------------------------------------------------------
_TIER1_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Reference\s*#\s*[\d]+\.[0-9a-f]+\.\d+\.[0-9a-f]+", re.I), "Akamai block (Reference #)"),
    (re.compile(r"Pardon\s+Our\s+Interruption", re.I), "Akamai challenge"),
    (re.compile(r"challenge-form.*?__cf_chl_f_tk=", re.I | re.DOTALL), "Cloudflare challenge form"),
    (re.compile(r'<span\s+class="cf-error-code">\d{4}</span>', re.I), "Cloudflare firewall block"),
    (re.compile(r"/cdn-cgi/challenge-platform/\S+orchestrate", re.I), "Cloudflare JS challenge"),
    (re.compile(r"window\._pxAppId\s*=", re.I), "PerimeterX block"),
    (re.compile(r"captcha\.px-cdn\.net", re.I), "PerimeterX captcha"),
    (re.compile(r"captcha-delivery\.com", re.I), "DataDome captcha"),
    (re.compile(r"_Incapsula_Resource", re.I), "Imperva/Incapsula block"),
    (re.compile(r"Incapsula\s+incident\s+ID", re.I), "Imperva/Incapsula incident"),
    (re.compile(r"Sucuri\s+WebSite\s+Firewall", re.I), "Sucuri firewall block"),
    (re.compile(r"KPSDK\.scriptStart\s*=\s*KPSDK\.now\(\)", re.I), "Kasada challenge"),
    (re.compile(r"blocked\s+by\s+network\s+security", re.I), "Network security block"),
    (re.compile(r"环境异常", re.I), "WeChat verification block"),
    (re.compile(r"完成验证后即可继续访问", re.I), "WeChat verification block"),
]

# ---------------------------------------------------------------------------
# Tier 2: Medium-confidence patterns — only short pages (< 10 KB)
# These terms appear in real content, so we gate on page size.
# ---------------------------------------------------------------------------
_TIER2_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Access\s+Denied", re.I), "Access Denied on short page"),
    (re.compile(r"Checking\s+your\s+browser", re.I), "Cloudflare browser check"),
    (re.compile(r"<title>\s*Just\s+a\s+moment", re.I), "Cloudflare interstitial"),
    (re.compile(r'class=["\']g-recaptcha["\']', re.I), "reCAPTCHA on block page"),
    (re.compile(r'class=["\']h-captcha["\']', re.I), "hCaptcha on block page"),
    (re.compile(r"Access\s+to\s+This\s+Page\s+Has\s+Been\s+Blocked", re.I), "PerimeterX block page"),
    (re.compile(r"blocked\s+by\s+security", re.I), "Blocked by security"),
    (re.compile(r"Request\s+unsuccessful", re.I), "Request unsuccessful (Imperva)"),
]

_TIER2_MAX_SIZE = 10_000

# ---------------------------------------------------------------------------
# Tier 3: Structural integrity — silent blocks, empty shells
# ---------------------------------------------------------------------------
_STRUCTURAL_MAX_SIZE = 50_000
_CONTENT_ELEMENTS_RE = re.compile(
    r"<(?:p|h[1-6]|article|section|li|td|a|pre)\b",
    re.I,
)
_SCRIPT_TAG_RE = re.compile(r"<script\b", re.I)
_BODY_RE = re.compile(r"<body\b", re.I)
# An interstitial that waits for JavaScript (a WAF verification, a loading screen) names no title, runs a few scripts
# and shows less than a short paragraph; a real page, however small, names itself.
_SHELL_MIN_SCRIPTS = 3
_SHELL_MAX_VISIBLE_CHARS = 200

# ---------------------------------------------------------------------------
# Error page title patterns
# ---------------------------------------------------------------------------
_ERROR_TITLE_RE = re.compile(
    r"\b(?:404|not\s+found|page\s+not\s+found|error|access\s+denied"
    r"|forbidden|unauthorized|50[023]|bad\s+gateway"
    r"|service\s+unavailable|temporarily\s+unavailable)\b",
    re.I,
)

_ERROR_CONTENT_KEYWORDS = frozenset(
    {
        "file not found",
        "page does not exist",
        "page not found",
        "access denied",
        "permission denied",
        "forbidden",
        "error occurred",
        "something went wrong",
        "oops",
    }
)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
_BLOCK_PAGE_MAX_SIZE = 5_000
_SERVER_ERROR_MIN_STATUS = 500
_EMPTY_CONTENT_THRESHOLD = 100
_SNIPPET_SIZE = 15_000
_DEEP_SCAN_THRESHOLD = 15_000
_DEEP_SCAN_LIMIT = 500_000
_DEEP_SNIPPET_SIZE = 30_000


def _scriptless_head(html: str) -> str:
    """The head of *html* without scripts and styles, so inline bundles cannot push block markers out of the window."""
    return strip_script_and_style(html[:_DEEP_SCAN_LIMIT])[:_DEEP_SNIPPET_SIZE]


def _looks_like_data(html: str) -> bool:
    """Check if content looks like a JSON/XML API response."""
    stripped = html.strip()
    if not stripped:
        return False
    if stripped[0] in ("{", "["):
        return True
    if stripped[:10].lower().startswith(("<html", "<!")):
        return bool(re.search(r"<body[^>]*>\s*<pre[^>]*>\s*[{\[]", stripped[:500], re.I))
    return stripped[0] == "<"


def _is_refusal_status(status_code: int | None) -> bool:
    """403 and every server error (5xx): the origin refused or failed the request."""
    return status_code is not None and (status_code == 403 or status_code >= _SERVER_ERROR_MIN_STATUS)


def _structural_integrity_check(html: str) -> tuple[bool, str]:
    """Tier 3: Catch pages that pass pattern detection but are structurally broken."""
    html_len = len(html)
    if html_len > _STRUCTURAL_MAX_SIZE or _looks_like_data(html):
        return False, ""

    if not _BODY_RE.search(html):
        return True, f"Structural: no <body> tag ({html_len} bytes)"

    body = body_content(html)
    visible_text = strip_tags(strip_script_and_style(html if body is None else body)).strip()
    visible_len = len(visible_text)

    signals: list[str] = []

    if visible_len < 50:
        signals.append("minimal_text")

    content_elements = len(_CONTENT_ELEMENTS_RE.findall(html))
    if content_elements == 0:
        signals.append("no_content_elements")

    script_count = len(_SCRIPT_TAG_RE.findall(html))
    if script_count > 0 and content_elements == 0 and visible_len < 100:
        signals.append("script_heavy_shell")

    signal_count = len(signals)
    if signal_count >= 2:
        return True, f"Structural: {', '.join(signals)} ({html_len} bytes, {visible_len} chars visible)"
    if signal_count == 1 and html_len < _BLOCK_PAGE_MAX_SIZE:
        return True, f"Structural: {signals[0]} on small page ({html_len} bytes, {visible_len} chars visible)"

    if (
        script_count >= _SHELL_MIN_SCRIPTS
        and visible_len < _SHELL_MAX_VISIBLE_CHARS
        and not (extract_title(html) or "").strip()
    ):
        detail = f"{script_count} scripts, {visible_len} chars visible, {html_len} bytes"
        return True, f"Structural: untitled script shell ({detail})"

    return False, ""


def _check_error_page(title: str | None, content: str | None) -> tuple[bool, str]:
    """Check for standard error pages (404, 500, etc.)."""
    if title and _ERROR_TITLE_RE.search(title):
        return True, f"Error page title: {title}"

    if content and len(content) < 100:
        content_lower = content.lower()
        for keyword in _ERROR_CONTENT_KEYWORDS:
            if keyword in content_lower:
                return True, f"Error page content ({len(content)} chars)"

    return False, ""


def check_challenge_headers(headers: Mapping[str, str] | None) -> str | None:
    """Protocol-level WAF challenge headers (Cloudflare / AWS WAF)."""
    if not headers:
        return None
    lowered = {k.lower(): v.strip().lower() for k, v in headers.items()}
    cf_mitigated = lowered.get("cf-mitigated", "")
    if "challenge" in cf_mitigated:
        return f"Cloudflare challenge header (cf-mitigated: {cf_mitigated})"
    aws_waf = lowered.get("x-amzn-waf-action", "")
    if "challenge" in aws_waf:
        return f"AWS WAF challenge header (x-amzn-waf-action: {aws_waf})"
    return None


def classify_block(
    status_code: int | None,
    html: str | None,
    *,
    title: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[BlockKind | None, str]:
    """Classify a fetch response as explicit site block, JS-required empty shell, generic error page, or normal content.

    Protocol-level signals (HTTP 403/429, ``cf-mitigated: challenge``, ``x-amzn-waf-action: challenge``,
    HTTP 202 + script shell, WAF markers) classify as ``"blocked"``.
    HTTP 200 structural empty shells / near-empty non-data pages classify as ``"needs_javascript"``.
    Plain HTTP 5xx server outages without WAF markers and generic non-access-denied error pages
    classify as ``"error_page"``.

    Returns:
        ``(kind, reason)`` where ``kind`` is ``"blocked"``, ``"needs_javascript"``, ``"error_page"``, or ``None``.
    """
    html = html or ""
    html_len = len(html)

    if status_code == 429:
        return "blocked", "HTTP 429 Too Many Requests"

    header_reason = check_challenge_headers(headers)
    if header_reason is not None:
        return "blocked", header_reason

    # --- Tier 1: High-confidence patterns (any page size) ---
    snippet = html[:_SNIPPET_SIZE]
    if snippet:
        for pattern, reason in _TIER1_PATTERNS:
            if pattern.search(snippet):
                return "blocked", reason

    # Large-page deep scan: strip scripts/styles and re-check Tier 1
    if html_len > _DEEP_SCAN_THRESHOLD:
        deep_snippet = _scriptless_head(html)
        for pattern, reason in _TIER1_PATTERNS:
            if pattern.search(deep_snippet):
                return "blocked", reason

    # --- HTTP 403 and 5xx: reject non-data HTML; only 403 or WAF-marked 5xx classify as explicit "blocked" ---
    if _is_refusal_status(status_code) and not _looks_like_data(html):
        default_kind: BlockKind = "blocked" if status_code == 403 else "error_page"
        check_snippet = _scriptless_head(html) if html_len > _TIER2_MAX_SIZE else snippet
        for pattern, reason in _TIER2_PATTERNS:
            if pattern.search(check_snippet):
                return "blocked", f"{reason} (HTTP {status_code}, {html_len} bytes)"
        if html_len < _EMPTY_CONTENT_THRESHOLD:
            return default_kind, f"HTTP {status_code} with near-empty response ({html_len} bytes)"
        return default_kind, f"HTTP {status_code} with HTML content ({html_len} bytes)"

    # --- Tier 2: medium-confidence on other 4xx/5xx + short page ---
    if status_code and status_code >= 400 and html_len < _TIER2_MAX_SIZE:
        for pattern, reason in _TIER2_PATTERNS:
            if pattern.search(snippet):
                return "blocked", f"{reason} (HTTP {status_code}, {html_len} bytes)"

    # --- HTTP 200 / 202 + near-empty ---
    if status_code in (200, 202):
        stripped = html.strip()
        if len(stripped) < _EMPTY_CONTENT_THRESHOLD and not _looks_like_data(html):
            kind: BlockKind = "blocked" if status_code == 202 else "needs_javascript"
            return kind, f"Near-empty content ({len(stripped)} bytes) with HTTP {status_code}"

    # --- Tier 3: Structural integrity ---
    blocked, reason = _structural_integrity_check(html)
    if blocked:
        # HTTP 202 with a script/empty shell is an AWS WAF challenge interstitial; HTTP 200 is a JS-rendered shell.
        kind = "blocked" if status_code == 202 else "needs_javascript"
        return kind, reason

    # --- Error page detection ---
    blocked, reason = _check_error_page(title, html if html_len < 200 else None)
    if blocked:
        access_denied = any(tok in reason.lower() for tok in ("access denied", "forbidden", "unauthorized"))
        return ("blocked" if access_denied else "error_page"), reason

    return None, ""


def is_blocked(
    status_code: int | None,
    html: str | None,
    *,
    title: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """Detect if a crawl result was blocked by anti-bot protection or is an error page.

    Uses layered detection to maximize coverage while minimizing false positives.

    Returns:
        (is_blocked, reason). reason is empty string when not blocked.
    """
    kind, reason = classify_block(status_code, html, title=title, headers=headers)
    return kind is not None, reason
