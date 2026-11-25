"""HTML to Markdown converter built on BeautifulSoup.

[INPUT]
- web_fetch.processing.html_to_markdown_helpers::ConvertOptions, BLOCK_TAGS, parse_ol_start, resolve_url, strip_non_visible_elements, tables_with_rows (POS: Shared configuration and pure helper functions for HTML2Markdown)
- web_fetch.processing.markdown_table::render_markdown_table (POS: Markdown table layout for converted HTML tables)

[OUTPUT]
- HTML2Markdown: HTML-to-Markdown converter for fetched page content; a table becomes one Markdown table that also lists the rows of its nested tables
- ConvertOptions: Re-exported conversion parameters dataclass

[POS]
HTML to Markdown conversion utilities for the web fetch processing pipeline.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterable
from typing import ClassVar, cast

from bs4 import BeautifulSoup, Comment, Tag
from bs4.element import NavigableString

from .html_to_markdown_helpers import (
    BLOCK_TAGS,
    ConvertOptions,
    parse_ol_start,
    resolve_url,
    strip_non_visible_elements,
    tables_with_rows,
)
from .markdown_table import render_markdown_table

_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
# The lookbehind pins a match to the start of a run of spaces. Without it every later space of a run that no newline
# follows starts another scan to the end of the run: quadratic in the run length.
_TRAILING_SPACES_RE = re.compile(r"(?<! ) +\n")


class HTML2Markdown:
    """Convert HTML to Markdown using BeautifulSoup.

    Designed as a drop-in replacement for the vendored html2text module,
    exposing the same ``handle()`` / ``update_params()`` API used by callers.
    """

    MAX_CONVERSION_DEPTH: ClassVar[int] = 128

    def __init__(self, baseurl: str = "", **kwargs: object) -> None:
        self._baseurl = baseurl
        self._opts = ConvertOptions()
        self._current_depth: int = 0
        # Number of table cells being converted: inside one, the rows of the enclosing table are already listed.
        self._cell_depth: int = 0
        self._tables_with_tr: set[int] = set()
        if kwargs:
            self.update_params(**kwargs)

    def update_params(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            if key == "preserve_tags":
                self._opts.preserve_tags = set(cast("Iterable[str]", value))
            elif hasattr(self._opts, key):
                setattr(self._opts, key, value)

    def handle(self, html_str: str) -> str:
        if not html_str:
            return ""

        self._current_depth = 0
        soup = BeautifulSoup(html_str, "html.parser")

        for tag in soup.find_all(["script", "style", "head"]):
            tag.decompose()
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        body = soup.find("body")
        root = body if body else soup

        strip_non_visible_elements(root)
        self._tables_with_tr = tables_with_rows(root)
        try:
            result = self._convert_node(root)
        finally:
            self._tables_with_tr = set()
        result = _TRAILING_SPACES_RE.sub("\n", result)
        result = _MULTI_NEWLINE_RE.sub("\n\n", result)
        return result.strip() + "\n" if result.strip() else ""

    def _convert_node(self, node: Tag | NavigableString) -> str:
        if isinstance(node, NavigableString):
            if isinstance(node, Comment):
                return ""
            text = str(node)
            text = html.unescape(text)
            text = re.sub(r"[ \t]+", " ", text)
            return text

        if self._current_depth >= self.MAX_CONVERSION_DEPTH:
            text = node.get_text(separator=" ", strip=True)
            return f" {html.unescape(text)} " if text else ""

        self._current_depth += 1
        try:
            tag_name = node.name
            if tag_name is None:
                return self._convert_children(node)

            if tag_name in self._opts.preserve_tags:
                return str(node)

            if self._cell_depth and (tag_name == "tr" or (tag_name == "table" and id(node) in self._tables_with_tr)):
                return ""

            handler = _TAG_HANDLERS.get(tag_name)
            if handler:
                return handler(self, node)

            if tag_name in BLOCK_TAGS:
                inner = self._convert_children(node)
                return f"\n\n{inner.strip()}\n\n" if inner.strip() else ""

            return self._convert_children(node)
        finally:
            self._current_depth -= 1

    def _convert_children(self, node: Tag) -> str:
        parts: list[str] = []
        for child in node.children:
            if isinstance(child, (NavigableString, Tag)):
                parts.append(self._convert_node(child))
        return "".join(parts)

    # ── Tag handlers ──────────────────────────────────────────

    def _handle_heading(self, tag: Tag) -> str:
        level = int(tag.name[1])
        inner = self._convert_children(tag).strip()
        if not inner:
            return ""
        prefix = "#" * level
        return f"\n\n{prefix} {inner}\n\n"

    def _handle_p(self, tag: Tag) -> str:
        inner = self._convert_children(tag).strip()
        if not inner:
            return ""
        if self._opts.single_line_break:
            return f"\n{inner}\n"
        return f"\n\n{inner}\n\n"

    def _handle_br(self, _tag: Tag) -> str:
        return "  \n"

    def _handle_hr(self, _tag: Tag) -> str:
        return "\n\n* * *\n\n"

    def _handle_blockquote(self, tag: Tag) -> str:
        inner = self._convert_children(tag).strip()
        if not inner:
            return ""
        lines = inner.split("\n")
        quoted = "\n".join(f"> {line}" for line in lines)
        return f"\n\n{quoted}\n\n"

    def _handle_pre(self, tag: Tag) -> str:
        code_tag = tag.find("code", recursive=False)
        if code_tag and isinstance(code_tag, Tag):
            lang = ""
            classes = code_tag.get("class")
            if isinstance(classes, list):
                for cls in classes:
                    if isinstance(cls, str) and cls.startswith("language-"):
                        lang = cls[9:]
                        break
                    elif isinstance(cls, str) and cls.startswith("lang-"):
                        lang = cls[5:]
                        break
            text = code_tag.get_text()
        else:
            text = tag.get_text()
            lang = ""

        if text and not text.endswith("\n"):
            text += "\n"
        return f"\n\n```{lang}\n{text}```\n\n"

    def _handle_code(self, tag: Tag) -> str:
        parent = tag.parent
        if parent and parent.name == "pre":
            return tag.get_text()
        text = tag.get_text()
        if not text:
            return ""
        if "`" in text:
            max_run = max((len(m) for m in re.findall(r"`+", text)), default=0)
            fence = "`" * (max_run + 1)
            needs_pad = text.startswith("`") or text.endswith("`")
            body = f" {text} " if needs_pad else text
            return f"{fence}{body}{fence}"
        return f"`{text}`"

    def _handle_emphasis(self, tag: Tag) -> str:
        if self._opts.ignore_emphasis:
            return self._convert_children(tag)
        inner = self._convert_children(tag).strip()
        if not inner:
            return ""
        return f"_{inner}_"

    def _handle_strong(self, tag: Tag) -> str:
        if self._opts.ignore_emphasis:
            return self._convert_children(tag)
        inner = self._convert_children(tag).strip()
        if not inner:
            return ""
        return f"**{inner}**"

    def _handle_strikethrough(self, tag: Tag) -> str:
        if self._opts.ignore_emphasis:
            return self._convert_children(tag)
        inner = self._convert_children(tag).strip()
        if not inner:
            return ""
        return f"~~{inner}~~"

    def _handle_link(self, tag: Tag) -> str:
        href = tag.get("href", "") or ""
        if isinstance(href, list):
            href = href[0] if href else ""

        if self._opts.ignore_links:
            return self._convert_children(tag)

        if self._opts.ignore_mailto_links and str(href).startswith("mailto:"):
            return self._convert_children(tag)

        if self._opts.skip_internal_links and str(href).startswith("#"):
            return self._convert_children(tag)

        href = resolve_url(self._baseurl, str(href))
        inner = self._convert_children(tag).strip()
        if not inner:
            inner = href

        title = tag.get("title", "")
        if title:
            return f'[{inner}]({href} "{title}")'
        return f"[{inner}]({href})"

    def _handle_img(self, tag: Tag) -> str:
        if self._opts.ignore_images:
            return ""
        src = tag.get("src", "") or ""
        if isinstance(src, list):
            src = src[0] if src else ""
        src = resolve_url(self._baseurl, str(src))
        alt = tag.get("alt", "") or ""
        if isinstance(alt, list):
            alt = " ".join(alt)
        title = tag.get("title", "")
        if title:
            return f'![{alt}]({src} "{title}")'
        return f"![{alt}]({src})"

    def _handle_ul(self, tag: Tag) -> str:
        items: list[str] = []
        for child in tag.children:
            if isinstance(child, Tag) and child.name == "li":
                inner = self._convert_children(child).strip()
                if inner:
                    indent_inner = inner.replace("\n", "\n  ")
                    items.append(f"* {indent_inner}")
        if not items:
            return ""
        return "\n\n" + "\n".join(items) + "\n\n"

    def _handle_ol(self, tag: Tag) -> str:
        items: list[str] = []
        start = parse_ol_start(tag.get("start"))
        idx = start
        for child in tag.children:
            if isinstance(child, Tag) and child.name == "li":
                inner = self._convert_children(child).strip()
                if inner:
                    pad = " " * (len(str(idx)) + 2)
                    indent_inner = inner.replace("\n", f"\n{pad}")
                    items.append(f"{idx}. {indent_inner}")
                    idx += 1
        if not items:
            return ""
        return "\n\n" + "\n".join(items) + "\n\n"

    def _handle_table(self, tag: Tag) -> str:
        # ``find_all`` reaches the rows of nested tables too, so they become rows of this table and a cell never
        # renders a nested table itself (see ``tables_with_rows``). Converting them inside the cell as well would
        # repeat the nested rows once per nesting level: exponential in the depth.
        rows: list[list[str]] = []
        for tr in tag.find_all("tr"):
            if not isinstance(tr, Tag):
                continue
            cells = [
                self._convert_cell(cell) for cell in tr.children if isinstance(cell, Tag) and cell.name in ("td", "th")
            ]
            if cells:
                rows.append(cells)

        if not rows:
            return self._convert_children(tag)
        return render_markdown_table(rows, has_header=tag.find("thead") is not None)

    def _convert_cell(self, cell: Tag) -> str:
        self._cell_depth += 1
        try:
            text = self._convert_children(cell).strip()
        finally:
            self._cell_depth -= 1
        return text.replace("|", "\\|").replace("\n", " ")

    def _handle_li(self, tag: Tag) -> str:
        return self._convert_children(tag)

    def _handle_sup(self, tag: Tag) -> str:
        if self._opts.include_sup_sub:
            inner = self._convert_children(tag).strip()
            return f"<sup>{inner}</sup>" if inner else ""
        return self._convert_children(tag)

    def _handle_sub(self, tag: Tag) -> str:
        if self._opts.include_sup_sub:
            inner = self._convert_children(tag).strip()
            return f"<sub>{inner}</sub>" if inner else ""
        return self._convert_children(tag)

    def _handle_q(self, tag: Tag) -> str:
        inner = self._convert_children(tag).strip()
        return f'"{inner}"' if inner else ""

    def _handle_abbr(self, tag: Tag) -> str:
        return self._convert_children(tag)

    def _handle_div(self, tag: Tag) -> str:
        inner = self._convert_children(tag)
        stripped = inner.strip()
        if not stripped:
            return ""
        if self._opts.single_line_break:
            return f"\n{stripped}\n"
        return f"\n\n{stripped}\n\n"


_TAG_HANDLERS: dict[str, Callable[[HTML2Markdown, Tag], str]] = {
    "h1": HTML2Markdown._handle_heading,
    "h2": HTML2Markdown._handle_heading,
    "h3": HTML2Markdown._handle_heading,
    "h4": HTML2Markdown._handle_heading,
    "h5": HTML2Markdown._handle_heading,
    "h6": HTML2Markdown._handle_heading,
    "p": HTML2Markdown._handle_p,
    "br": HTML2Markdown._handle_br,
    "hr": HTML2Markdown._handle_hr,
    "blockquote": HTML2Markdown._handle_blockquote,
    "pre": HTML2Markdown._handle_pre,
    "code": HTML2Markdown._handle_code,
    "em": HTML2Markdown._handle_emphasis,
    "i": HTML2Markdown._handle_emphasis,
    "u": HTML2Markdown._handle_emphasis,
    "strong": HTML2Markdown._handle_strong,
    "b": HTML2Markdown._handle_strong,
    "s": HTML2Markdown._handle_strikethrough,
    "del": HTML2Markdown._handle_strikethrough,
    "strike": HTML2Markdown._handle_strikethrough,
    "a": HTML2Markdown._handle_link,
    "img": HTML2Markdown._handle_img,
    "ul": HTML2Markdown._handle_ul,
    "ol": HTML2Markdown._handle_ol,
    "li": HTML2Markdown._handle_li,
    "table": HTML2Markdown._handle_table,
    "div": HTML2Markdown._handle_div,
    "sup": HTML2Markdown._handle_sup,
    "sub": HTML2Markdown._handle_sub,
    "q": HTML2Markdown._handle_q,
    "abbr": HTML2Markdown._handle_abbr,
}
