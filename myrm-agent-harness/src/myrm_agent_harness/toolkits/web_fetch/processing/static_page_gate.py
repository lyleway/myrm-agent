"""Static long-page fallback gate for L1 HTTP fetches.

[INPUT]
- web_fetch.fetchers.protocols::FetcherType (POS: Fetcher type enum)
- utils.markdown_links::replace_markdown_links (POS: Linear-time Markdown link scan)

[OUTPUT]
- count_visible_markdown_chars: counts visible non-punctuation characters in Markdown (excluding image tags and link URLs)
- should_fallback_to_raw_markdown: decides whether an L1 2xx page pruned to empty should deliver its raw Markdown

[POS]
Fallback gate in ContentPipeline. Prevents long static directory/index pages (e.g. man7, Gutenberg, Unicode tables)
from being treated as L1 failures and escalating to the remote fallback when boilerplate pruning strips their link-heavy DOM.
"""

from __future__ import annotations

import unicodedata

from myrm_agent_harness.utils.markdown_links import replace_markdown_links

from ..fetchers.protocols import FetcherType

_MIN_VISIBLE_CHARS = 3_000
_MIN_TEXT_TO_HTML_RATIO = 0.04


def count_visible_markdown_chars(raw_markdown: str) -> int:
    """Count visible non-punctuation, non-whitespace characters in *raw_markdown*.

    Image syntax is dropped entirely and inline links contribute only their label text,
    so long URLs or base64/image boilerplate cannot inflate the visible character count.
    """
    if not raw_markdown:
        return 0
    without_images = replace_markdown_links(
        raw_markdown, lambda _label, _target, _original: " ", image=True, label_min=0, target_min=0
    )
    text_only = replace_markdown_links(
        without_images, lambda label, _target, _original: label, label_min=0, target_min=0
    )
    count = 0
    for ch in text_only:
        if ch.isspace():
            continue
        cat = unicodedata.category(ch)
        if cat[0] in ("P", "S", "C", "Z"):
            continue
        count += 1
    return count


def should_fallback_to_raw_markdown(
    *,
    fetcher_type: FetcherType,
    status_code: int,
    raw_markdown: str,
    html_length: int,
) -> bool:
    """Return True when an L1 2xx page with empty pruned output is a real long static page."""
    if fetcher_type is not FetcherType.HTTP:
        return False
    if not (200 <= status_code < 300):
        return False
    if html_length <= 0 or not raw_markdown:
        return False

    visible_chars = count_visible_markdown_chars(raw_markdown)
    if visible_chars < _MIN_VISIBLE_CHARS:
        return False
    return (visible_chars / html_length) >= _MIN_TEXT_TO_HTML_RATIO
