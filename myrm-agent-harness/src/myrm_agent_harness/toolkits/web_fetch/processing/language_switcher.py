"""Language-switcher list removal for fetched HTML pages.

[INPUT]
- bs4::Tag (POS: Parsed HTML DOM node)

[OUTPUT]
- strip_language_switchers: removes boilerplate language-switcher ``<ul>`` / ``<ol>`` lists from a page body

[POS]
Pre-pruning noise filter in the web fetch processing pipeline. Strips MediaWiki-style inter-language link lists
that otherwise consume the majority of the delivery budget ahead of the article lead.
"""

from __future__ import annotations

from bs4 import Tag

_MIN_LIST_ITEMS = 8
_MIN_HREFLANG_RATIO = 0.8
_MAX_PAGE_TEXT_SHARE = 0.5


def _is_language_switcher(list_tag: Tag) -> bool:
    items = [child for child in list_tag.children if isinstance(child, Tag) and child.name == "li"]
    if len(items) < _MIN_LIST_ITEMS:
        return False
    hreflang_items = sum(1 for item in items if item.find("a", attrs={"hreflang": True}) is not None)
    return (hreflang_items / len(items)) >= _MIN_HREFLANG_RATIO


def strip_language_switchers(body: Tag) -> int:
    """Remove language-switcher ``<ul>`` / ``<ol>`` lists from *body* in place.

    Guards against emptying a translation-index page: any candidate list whose text accounts for more than half
    of the page's visible text is kept. Returns the number of removed lists.
    """
    candidates = [tag for tag in body.find_all(("ul", "ol")) if isinstance(tag, Tag) and _is_language_switcher(tag)]
    if not candidates:
        return 0

    page_text_len = len(body.get_text(strip=True))
    if page_text_len == 0:
        return 0

    removed = 0
    for candidate in candidates:
        if candidate.parent is None:
            continue
        list_text_len = len(candidate.get_text(strip=True))
        if (list_text_len / page_text_len) > _MAX_PAGE_TEXT_SHARE:
            continue
        candidate.decompose()
        removed += 1
    return removed
