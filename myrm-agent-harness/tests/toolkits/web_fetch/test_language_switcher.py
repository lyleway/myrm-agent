"""Tests for language-switcher list removal (R18)."""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from myrm_agent_harness.toolkits.web_fetch.fetchers.protocols import FetcherType, FetchResult
from myrm_agent_harness.toolkits.web_fetch.processing.content_pruning import ContentPruningFilter
from myrm_agent_harness.toolkits.web_fetch.processing.language_switcher import strip_language_switchers
from myrm_agent_harness.toolkits.web_fetch.processing.pipeline import ContentPipeline

_LANGS = [
    ("en", "English"),
    ("zh", "中文"),
    ("ja", "日本語"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("ko", "한국어"),
    ("ru", "Русский"),
    ("pt", "Português"),
    ("it", "Italiano"),
]


def _lang_list(entries: list[tuple[str, str]], tag: str = "ul") -> str:
    items = "".join(
        f'<li><a href="https://{code}.example.org/wiki/Topic" hreflang="{code}">{name}</a></li>'
        for code, name in entries
    )
    return f"<{tag}>{items}</{tag}>"


def test_removes_mediawiki_style_language_list_and_preserves_lead() -> None:
    lead = "Python is a high-level, general-purpose programming language. " * 20
    html = f"<html><body>{_lang_list(_LANGS)}<article><h1>Python</h1><p>{lead}</p></article></body></html>"

    kept = "".join(ContentPruningFilter().filter_content(html))

    assert "Python is a high-level" in kept
    assert "Français" not in kept
    assert "Português" not in kept


def test_keeps_short_language_list_under_eight_items() -> None:
    lead = "Main documentation body text explaining the API. " * 20
    html = f"<html><body>{_lang_list(_LANGS[:7])}<article><p>{lead}</p></article></body></html>"
    soup = BeautifulSoup(html, "lxml")
    assert isinstance(soup.body, Tag)

    removed = strip_language_switchers(soup.body)

    assert removed == 0
    assert "Français" in soup.body.get_text()


def test_keeps_mixed_list_below_80_percent_hreflang() -> None:
    items = "".join(f'<li><a href="/{c}" hreflang="{c}">{n}</a></li>' for c, n in _LANGS[:7])
    items += "<li><a href='/about'>About</a></li><li><a href='/contact'>Contact</a></li><li><a href='/faq'>FAQ</a></li>"
    html = f"<html><body><ul>{items}</ul><article><p>{'Article body ' * 40}</p></article></body></html>"
    soup = BeautifulSoup(html, "lxml")
    assert isinstance(soup.body, Tag)

    assert strip_language_switchers(soup.body) == 0


def test_guard_preserves_translation_index_page() -> None:
    """守卫：当语言列表占整页文字超过 50% 时（翻译索引页本身），必须保留。"""
    html = f"<html><body><h1>Translations</h1>{_lang_list(_LANGS)}</body></html>"
    soup = BeautifulSoup(html, "lxml")
    assert isinstance(soup.body, Tag)

    assert strip_language_switchers(soup.body) == 0
    assert "English" in soup.body.get_text()
    assert "Italiano" in soup.body.get_text()


def test_raw_markdown_mode_does_not_strip_language_list() -> None:
    lead = "Article content explaining systems design in detail. " * 20
    html = f"<html><body>{_lang_list(_LANGS)}<article><p>{lead}</p></article></body></html>"
    result = FetchResult(html=html, url="https://en.example.org/wiki/Topic", fetcher_type=FetcherType.HTTP)

    raw_doc = ContentPipeline(use_raw_markdown=True).process(result)
    fit_doc = ContentPipeline(use_raw_markdown=False).process(result)

    assert raw_doc is not None and "Français" in raw_doc.page_content
    assert fit_doc is not None and "Français" not in fit_doc.page_content
    assert "Article content explaining" in fit_doc.page_content
