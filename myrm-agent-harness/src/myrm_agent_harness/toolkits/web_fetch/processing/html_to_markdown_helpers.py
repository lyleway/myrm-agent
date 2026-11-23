"""Options and pure DOM helpers for HTML-to-Markdown conversion.

[INPUT]
- bs4::Tag (POS: Parsed HTML DOM node)

[OUTPUT]
- ConvertOptions: conversion parameters for ``HTML2Markdown``
- BLOCK_TAGS: block-level HTML tag names
- parse_ol_start: parses ``<ol start>`` per HTML signed integer rules
- resolve_url: resolves relative URLs against a base URL
- tables_with_rows: iterative bottom-up set of ``<table>`` ids containing a ``<tr>`` descendant

[POS]
Shared configuration and pure helper functions for ``HTML2Markdown``.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

from bs4 import Tag


@dataclass
class ConvertOptions:
    """Conversion parameters for ``HTML2Markdown``."""

    body_width: int = 0
    ignore_emphasis: bool = False
    ignore_links: bool = False
    ignore_images: bool = False
    protect_links: bool = False
    single_line_break: bool = False
    mark_code: bool = False
    escape_snob: bool = False
    skip_internal_links: bool = True
    ignore_mailto_links: bool = True
    escape_backslash: bool = False
    escape_dot: bool = False
    escape_plus: bool = False
    escape_dash: bool = False
    include_sup_sub: bool = False
    preserve_tags: set[str] = field(default_factory=set)
    handle_code_in_pre: bool = False


BLOCK_TAGS: frozenset[str] = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "details",
        "dialog",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hgroup",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
    }
)

# HTML spec rules for parsing signed integers on <ol start>: optional whitespace, optional sign, contiguous ASCII digits.
_OL_START_RE = re.compile(r"^\s*([+-]?[0-9]+)")


def parse_ol_start(raw: object) -> int:
    """Parse ``<ol start>`` per HTML signed integer rules, defaulting to 1."""
    if raw is None:
        return 1
    match = _OL_START_RE.match(str(raw))
    return int(match.group(1)) if match else 1


def resolve_url(base: str, url: str) -> str:
    """Resolve relative *url* against *base*, preserving absolute and special schemes."""
    if not url or url.startswith(("http://", "https://", "mailto:", "data:", "//")):
        return url
    if not base:
        return url
    return urllib.parse.urljoin(base, url)


def tables_with_rows(root: Tag) -> set[int]:
    """Return ``id(table)`` for every ``<table>`` below *root* that has a ``<tr>`` descendant.

    Single iterative bottom-up pass: avoids O(depth^2) repeated subtree scans on nested tables.
    """
    order: list[Tag] = []
    stack: list[Tag] = [root]
    while stack:
        current = stack.pop()
        order.append(current)
        for child in current.children:
            if isinstance(child, Tag):
                stack.append(child)

    has_tr: set[int] = set()
    tables_with_tr: set[int] = set()
    for node in reversed(order):
        node_has_tr = node.name == "tr" or any(
            isinstance(child, Tag) and id(child) in has_tr for child in node.children
        )
        if node_has_tr:
            node_id = id(node)
            has_tr.add(node_id)
            if node.name == "table":
                tables_with_tr.add(node_id)
    return tables_with_tr
