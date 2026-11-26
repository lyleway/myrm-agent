"""Iterative DOM subtree metrics for boilerplate pruning.

[INPUT]
- bs4::Tag, bs4.element::NavigableString (POS: Parsed HTML DOM nodes)

[OUTPUT]
- dom_path_units: counts ancestor-descendant element pairs up to a ceiling
- compute_subtree_metrics: computes per-node ``(text_len, tag_len)`` in one bottom-up pass without allocating temporary Tags

[POS]
Performance-critical DOM measurement helpers for ``ContentPruningFilter``.
"""

from __future__ import annotations

from bs4 import Tag
from bs4.element import NavigableString


def dom_path_units(root: Tag, limit: int) -> int:
    """Count the ancestor-descendant element pairs below *root* (the sum of every element's depth).

    Counts iteratively and stops once above *limit*, so measuring a pathologically deep page is cheap itself.
    """
    total = 0
    stack: list[tuple[Tag, int]] = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        for child in node.children:
            if isinstance(child, Tag):
                total += depth + 1
                if total > limit:
                    return total
                stack.append((child, depth + 1))
    return total


def _wrapper_len(node: Tag) -> int:
    """Compute exact outer ``<name ...></name>`` length matching BeautifulSoup's minimal HTML formatter."""
    length = len(node.name) * 2 + 5
    attrs = node.attrs
    if not attrs:
        return length
    for key, val in attrs.items():
        if val is None:
            length += 1 + len(key)
            continue
        raw = " ".join(str(item) for item in val) if isinstance(val, list) else str(val)
        escaped = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if '"' in escaped and "'" not in escaped:
            # BeautifulSoup wraps attribute values containing only double quotes in single quotes.
            length += 1 + len(key) + 3 + len(escaped)
        elif '"' in escaped:
            length += 1 + len(key) + 3 + len(escaped.replace('"', "&quot;"))
        else:
            length += 1 + len(key) + 3 + len(escaped)
    return length


def compute_subtree_metrics(root: Tag) -> tuple[dict[int, int], dict[int, int]]:
    """Compute ``(text_len, tag_len)`` for every Tag below *root* in one iterative bottom-up pass.

    Matches ``len(node.get_text(strip=True))`` and ``len(node.encode_contents().decode("utf-8"))``
    without re-walking or re-encoding subtrees at every ancestor.
    """
    order: list[Tag] = []
    stack: list[Tag] = [root]
    while stack:
        current = stack.pop()
        order.append(current)
        for child in current.children:
            if isinstance(child, Tag):
                stack.append(child)

    text_lens: dict[int, int] = {}
    tag_lens: dict[int, int] = {}
    outer_lens: dict[int, int] = {}

    for node in reversed(order):
        t_len = 0
        inner_html_len = 0
        has_children = False
        for child in node.children:
            has_children = True
            if isinstance(child, Tag):
                cid = id(child)
                t_len += text_lens[cid]
                inner_html_len += outer_lens[cid]
            elif type(child) is NavigableString:
                t_len += len(str(child).strip())
                inner_html_len += len(child.output_ready())
            elif isinstance(child, NavigableString):
                inner_html_len += len(child.output_ready())

        nid = id(node)
        text_lens[nid] = t_len
        tag_lens[nid] = inner_html_len
        outer_lens[nid] = len(node.decode()) if not has_children else (_wrapper_len(node) + inner_html_len)

    return text_lens, tag_lens
