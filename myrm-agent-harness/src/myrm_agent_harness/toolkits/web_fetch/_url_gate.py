"""Pre-fetch URL gate of the web fetch tool: wrapper unwrapping, HTTP(S) canonical spelling, host blocklist, SSRF guard, exfiltration check, and non-document resource pre-rejection.

[INPUT]
- utils.url_syntax::parse_outbound_url (POS: the one canonical spelling of an outbound URL)
- toolkits.browser.domain_filter::DomainBlocklist (POS: hostname blocklist for benchmark decontamination) [lazy]
- core.security.guards.ssrf::async_validate_url_for_ssrf (POS: Async SSRF classification of a URL without blocking the event loop) [lazy]
- utils.url_utils::check_url_exfiltration, sanitize_url_for_error (POS: secret-in-URL detection) [lazy]
- utils.errors::ToolError, ToolErrorCategory

[OUTPUT]
- validate_urls: the URLs in canonical spelling, or a ToolError naming the first URL that may not be fetched

[POS]
Runs before any request of the web fetch tool. A URL is unwrapped from common model wrappers (Markdown links,
matching quotes/angle brackets, unbalanced trailing parentheses) and judged in the canonical HTTP(S) spelling
it is fetched in, so the blocklist, the SSRF guard, and the HTTP fetcher all read the same host. Non-document
binary/media extensions (audio, video, fonts, archives, executables) are refused after security gates and before
any network transfer.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from myrm_agent_harness.utils.errors import ToolError, ToolErrorCategory
from myrm_agent_harness.utils.url_syntax import parse_outbound_url

_WEB_FETCH_ALLOWED_SCHEMES = frozenset({"http", "https"})
_HTTP_PREFIX_RE = re.compile(r"^https?://", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r'^\[[^\]]*\]\(\s*<?(https?://[^\s)>]+)>?(?:\s+["\'][^"\']*["\'])?\s*\)$', re.IGNORECASE)
_MATCHING_WRAPPERS: tuple[tuple[str, str], ...] = (("<", ">"), ('"', '"'), ("'", "'"), ("`", "`"))
_TRAILING_PROSE_PUNCT = frozenset({",", ";", "!"})

# Extensions that never carry a readable webpage or supported document (PDF/Office/images stay allowed for binary_router).
_NON_DOCUMENT_EXTENSIONS = frozenset(
    {
        # audio
        "aac",
        "flac",
        "m4a",
        "mp3",
        "ogg",
        "opus",
        "wav",
        "wma",
        # video
        "avi",
        "flv",
        "m4v",
        "mkv",
        "mov",
        "mp4",
        "mpeg",
        "mpg",
        "webm",
        "wmv",
        # fonts
        "eot",
        "otf",
        "ttf",
        "woff",
        "woff2",
        # archives / binaries / installers
        "7z",
        "apk",
        "bin",
        "bz2",
        "deb",
        "dmg",
        "exe",
        "gz",
        "iso",
        "msi",
        "pkg",
        "rar",
        "rpm",
        "tar",
        "tgz",
        "wasm",
        "xz",
        "zip",
    }
)


def _unwrap_model_url(raw: str) -> str:
    """Safely strip model-injected wrappers (Markdown link, matching quotes/angles, trailing prose punctuation).

    Only unwraps when the inner candidate starts with ``http://`` or ``https://``, so non-HTTP schemes and bare
    hostnames stay untouched for ``parse_outbound_url`` to reject cleanly.
    """
    value = raw.strip()
    md = _MARKDOWN_LINK_RE.match(value)
    if md:
        value = md.group(1)

    for left, right in _MATCHING_WRAPPERS:
        if len(value) >= 2 and value.startswith(left) and value.endswith(right):
            candidate = value[1:-1].strip()
            if _HTTP_PREFIX_RE.match(candidate):
                value = candidate
                break

    if not _HTTP_PREFIX_RE.match(value):
        return value

    # Drop trailing prose punctuation and unbalanced closing parentheses without touching balanced URLs like Wikipedia.
    while value and value[-1] in _TRAILING_PROSE_PUNCT:
        value = value[:-1]
    unbalanced_closes = value.count(")") - value.count("(")
    while unbalanced_closes > 0 and value.endswith(")"):
        value = value[:-1]
        unbalanced_closes -= 1
    while value and value[-1] in _TRAILING_PROSE_PUNCT:
        value = value[:-1]
    return value


def _non_document_extension(canonical_url: str) -> str | None:
    """Return the lowercased non-document extension of *canonical_url*'s path, or None."""
    path = urlsplit(canonical_url).path
    last_segment = path.rsplit("/", 1)[-1]
    dot = last_segment.rfind(".")
    if dot <= 0:
        return None
    ext = last_segment[dot + 1 :].lower()
    return ext if ext in _NON_DOCUMENT_EXTENSIONS else None


async def validate_urls(
    urls: list[str],
    *,
    blocked_hostnames: tuple[str, ...] | None,
    allow_private_networks: bool,
) -> list[str]:
    """Refuse URLs that the host blocklist, SSRF guard, exfiltration check or resource preflight does not allow.

    Returns the URLs in canonical spelling: what was judged here is what gets fetched.
    """
    targets = []
    for url in urls:
        cleaned = _unwrap_model_url(url)
        target, error = parse_outbound_url(cleaned)
        if target is None:
            raise ToolError(
                f"URL blocked: {error} — {url}",
                user_hint="The URL is not a valid http(s) address. Use a different URL.",
            )
        scheme = target.url.split(":", 1)[0]
        if scheme not in _WEB_FETCH_ALLOWED_SCHEMES:
            raise ToolError(
                f"URL blocked: Blocked URL scheme: {scheme} — {url}",
                user_hint="Only http:// and https:// URLs are supported by web_fetch.",
            )
        targets.append(target)
    canonical_urls = [target.url for target in targets]

    if blocked_hostnames:
        from myrm_agent_harness.toolkits.browser.domain_filter import DomainBlocklist

        blocklist = DomainBlocklist.from_strings(blocked_hostnames)
        for target in targets:
            if blocklist.is_blocked(target.hostname):
                raise ToolError(
                    f"URL blocked: {target.url}",
                    user_hint="The URL host is excluded from fetching. Use a different URL.",
                    error_code="BENCHMARK_BLOCKED_HOST",
                    diagnostic_info={
                        "error_category": ToolErrorCategory.BENCHMARK_BLOCKED.value,
                        "hostname": target.hostname,
                    },
                )

    if not allow_private_networks:
        from myrm_agent_harness.core.security.guards.ssrf import async_validate_url_for_ssrf
        from myrm_agent_harness.utils.logger_utils import get_agent_logger
        from myrm_agent_harness.utils.url_utils import check_url_exfiltration, sanitize_url_for_error

        for url in canonical_urls:
            verdict = await async_validate_url_for_ssrf(url)
            if not verdict.safe:
                raise ToolError(
                    f"URL blocked (SSRF protection): {verdict.error} — {url}",
                    user_hint="The URL is blocked for security reasons. Use a different, publicly accessible URL.",
                )

            exfiltration_warnings = check_url_exfiltration(url, allow_private_networks=allow_private_networks)
            if exfiltration_warnings:
                logger = get_agent_logger(__name__)
                safe_url = sanitize_url_for_error(url)
                logger.warning(" Potential data exfiltration detected in URL: %s", safe_url)
                for warning in exfiltration_warnings:
                    logger.warning(" - %s", warning)
                raise ToolError(
                    f"URL blocked (data exfiltration detection): {'; '.join(exfiltration_warnings)} — {safe_url}",
                    user_hint="The URL appears to contain sensitive data. Remove secrets from the URL.",
                )

    for url in canonical_urls:
        ext = _non_document_extension(url)
        if ext is not None:
            raise ToolError(
                f"URL skipped: unsupported non-document resource (.{ext}) — {url}",
                user_hint=(
                    f"The URL points to a .{ext} media, font, archive, or binary file that web_fetch cannot read as "
                    "a webpage or document."
                ),
            )
    return canonical_urls
