"""L4 remote escalation response content validator.

[INPUT]
- (none — pure standard library)

[OUTPUT]
- is_valid_escalation_content: Rejects provider-returned error, challenge, login, and empty-shell pages.

[POS]
Central L4 response quality gate invoked by FetchEngineEscalationMixin before accepting any remote provider result.
"""

from __future__ import annotations

import re

_MIN_SUBSTANTIVE_CHARS = 500

_PROVIDER_WARNING_PREFIXES = (
    "warning: target url returned error 403",
    "warning: target url returned error 429",
    "warning: target url returned error 5",
    "warning: target url returned error 401",
    "warning: target url returned error 404",
)

_PROVIDER_SOFT_WARNING_PREFIXES = (
    "warning: this page maybe not yet fully loaded",
    "warning: target url returned error",
)

_BLOCK_TITLE_MARKERS = (
    "百度安全验证",
    "安全验证",
    "just a moment",
    "attention required",
    "access denied",
    "sign in to continue",
    "sign in",
    "log in",
    "continue shopping",
    "robot or human",
    "verify you are human",
    "403 forbidden",
    "404 not found",
    "page not found",
    "error 403",
    "error 404",
    "cloudflare",
    "captcha",
)

_JINA_HEADER_LINE_RE = re.compile(
    r"^(?:Title|URL Source|Published Time|Warning|Markdown Content):.*$",
    re.IGNORECASE,
)


def _extract_visible_body(content: str) -> tuple[str, str, list[str]]:
    """Separate Jina-style metadata header lines from visible body text."""
    extracted_title = ""
    warnings: list[str] = []
    body_lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("warning:"):
            warnings.append(lower)
            continue
        if lower.startswith("title:") and not extracted_title:
            extracted_title = stripped.partition(":")[2].strip()
            continue
        if _JINA_HEADER_LINE_RE.match(stripped):
            continue
        body_lines.append(stripped)
    body = "\n".join(ln for ln in body_lines if ln).strip()
    return body, extracted_title, warnings


def is_valid_escalation_content(content: str, *, title: str = "") -> tuple[bool, str]:
    """Validate L4 remote provider content before accepting it as a fetched page.

    Returns ``(True, "ok")`` when substantive, or ``(False, reason)`` when the
    provider returned an error, WAF challenge, login wall, or empty shell.
    """
    stripped = content.strip()
    if not stripped:
        return False, "empty"

    body, header_title, warnings = _extract_visible_body(stripped)
    effective_title = (title or header_title).strip().lower()
    body_len = len(body)

    for warning in warnings:
        if warning.startswith(_PROVIDER_WARNING_PREFIXES):
            return False, "provider_error_warning"
        if warning.startswith(_PROVIDER_SOFT_WARNING_PREFIXES) and body_len < _MIN_SUBSTANTIVE_CHARS:
            return False, "provider_incomplete_warning"

    if not body:
        return False, "empty_body"

    if body_len < _MIN_SUBSTANTIVE_CHARS:
        for marker in _BLOCK_TITLE_MARKERS:
            if marker in effective_title:
                return False, f"block_title:{marker}"
        first_line = body.splitlines()[0].strip().lstrip("# ").lower()
        for marker in _BLOCK_TITLE_MARKERS:
            if marker in first_line:
                return False, f"block_heading:{marker}"

    return True, "ok"
