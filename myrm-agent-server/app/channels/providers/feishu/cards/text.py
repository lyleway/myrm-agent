"""[INPUT]
- myrm_agent_harness.utils.markdown_links::mask_unclosable_openers (POS: 线性时间的链接扫描，未闭合的 `[` 不会拖慢匹配)

[OUTPUT]
- resolve_send_mode: Determine Feishu message send mode based on context.
- has_rich_text: Detect if content has rich formatting that benefits from post type.
- _parse_post_line: Parse a single line of Markdown into Feishu post elements.
- _bolded: Add the bold style to a post element.
- build_post_content: Convert Markdown-like text to Feishu post (rich text) format.

[POS]
飞书卡片子包的一部分；由 __init__.py 聚合导出。
"""

from __future__ import annotations

import re
from typing import Literal

from myrm_agent_harness.utils.markdown_links import mask_unclosable_openers

SendMode = Literal["reply", "root_create", "create"]


# ── Send mode resolution ─────────────────────────────────────────


def resolve_send_mode(
    *,
    reply_to_id: str | None = None,
    root_id: str | None = None,
) -> SendMode:
    """Determine Feishu message send mode based on context.

    Priority: reply > root_create > create.
    """
    if reply_to_id:
        return "reply"
    if root_id:
        return "root_create"
    return "create"


# ── Post (rich text) format builders ─────────────────────────────

# Bold, strikethrough and code spans cost time in proportion to the line however broken their delimiters are. Links
# (lazy label and target, cubic: 1,000 repeats of ``[a](`` take 4.6 s) and ``_italic_`` (quadratic: 8,000 characters
# of `` _a`` take 0.1 s) do not, so each has its own scan below.
_PLAIN_EMPHASIS_RE = re.compile(r"\*\*.+?\*\*" r"|~~.+?~~" r"|`[^`]+`")
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_POST_INLINE_RE = re.compile(
    r"(\*\*(.+?)\*\*)" r"|(~~(.+?)~~)" r"|(`([^`]+)`)" r"|(\[(.+?)\]\((.+?)\))",
)


def _is_word_char(char: str) -> bool:
    """``\\w`` for one character: what ``re`` counts as a word character."""
    return char.isalnum() or char == "_"


def _has_link(line: str) -> bool:
    """``\\[.+?\\]\\(.+?\\)`` finds something in ``line``: an opener survives the masking of those that cannot link."""
    return "[" in mask_unclosable_openers(line, lazy_target=True)


def _has_underscore_emphasis(line: str) -> bool:
    """``(?<!\\w)_.+?_(?!\\w)`` finds something in ``line``: a "_" not behind a word character, and a "_" not before one at least two characters on."""
    last_closer = line.rfind("_")
    while last_closer != -1 and last_closer + 1 < len(line) and _is_word_char(line[last_closer + 1]):
        last_closer = line.rfind("_", 0, last_closer)
    opener = line.find("_")
    while 0 <= opener <= last_closer - 2:
        if opener == 0 or not _is_word_char(line[opener - 1]):
            return True
        opener = line.find("_", opener + 1)
    return False


def has_rich_text(content: str) -> bool:
    """Detect if content has rich formatting that benefits from post type."""
    return bool(
        _PLAIN_EMPHASIS_RE.search(content)
        or _HEADING_RE.search(content)
        or any(_has_link(line) or _has_underscore_emphasis(line) for line in content.split("\n"))
    )


def _span_text(line: str, match: re.Match[str], group: int = 0) -> str:
    """The part of ``line`` that ``group`` of ``match`` covers; ``match`` was found on a masked copy of ``line``."""
    return line[match.start(group) : match.end(group)]


def _parse_post_line(line: str) -> list[dict[str, object]]:
    """Parse a single line of Markdown into Feishu post elements."""
    elements: list[dict[str, object]] = []
    pos = 0
    # Openers that no link can start at are masked: the link branch would read the rest of the line from each of them.
    for m in _POST_INLINE_RE.finditer(mask_unclosable_openers(line, lazy_target=True)):
        if m.start() > pos:
            elements.append({"tag": "text", "text": line[pos : m.start()]})
        if m.group(2) is not None:
            elements.append({"tag": "text", "text": _span_text(line, m, 2), "style": ["bold"]})
        elif m.group(4) is not None:
            elements.append({"tag": "text", "text": _span_text(line, m, 4), "style": ["lineThrough"]})
        elif m.group(6) is not None:
            # A post text element has no code style (Feishu ignores unknown styles), so the backticks stay as the cue.
            elements.append({"tag": "text", "text": _span_text(line, m)})
        elif m.group(8) is not None:
            elements.append({"tag": "a", "text": _span_text(line, m, 8), "href": _span_text(line, m, 9)})
        pos = m.end()
    if pos < len(line):
        elements.append({"tag": "text", "text": line[pos:]})
    return elements or [{"tag": "text", "text": line}]


def _bolded(element: dict[str, object]) -> dict[str, object]:
    """``element`` with the bold style added to the styles it has."""
    styles = element.get("style")
    existing = [str(style) for style in styles] if isinstance(styles, list) else []
    return {**element, "style": list(dict.fromkeys(["bold", *existing]))}


def build_post_content(content: str) -> dict[str, object]:
    """Convert Markdown-like text to Feishu post (rich text) format.

    Produces a post payload with zh_cn locale. Handles bold, strikethrough, inline code,
    links and headings (bold, with their own inline spans) by mapping to Feishu post tags.
    """
    paragraphs: list[list[dict[str, object]]] = []
    for line in content.split("\n"):
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            paragraphs.append([_bolded(element) for element in _parse_post_line(line[heading_match.end() :])])
        else:
            paragraphs.append(_parse_post_line(line))
    return {"zh_cn": {"title": "", "content": paragraphs}}
