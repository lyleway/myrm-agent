"""Plain heading titles: a Markdown heading line with its formatting taken off.

[INPUT]
- re (POS: standard library regular expressions)
- myrm_agent_harness.utils.markdown_links::replace_markdown_links (POS: linear-time Markdown link scan)

[OUTPUT]
- clean_heading_text: the title a heading line reads as, without its `#` marker, wikilinks, links, images, code spans, emphasis and strikethrough

[POS]
A backlink names the section it sits in by the heading above it, and that heading comes from the text of any page,
so every pattern here must cost time in proportion to the heading's length: both link syntaxes are scanned
by position, not by a pattern that restarts from every unclosed `[`.
"""

from __future__ import annotations

import re

from myrm_agent_harness.utils.markdown_links import replace_markdown_links


def _strip_wikilinks(text: str) -> str:
    r"""``text`` with ``[[target|display]]`` replaced by ``display`` and ``[[target]]`` by ``target``.

    Reads what ``\[\[(?:[^|\]]*\|)?([^\]]+)\]\]`` reads. A wikilink ends at the first ``]`` after its ``[[`` and needs a
    second ``]`` right behind it, so every ``[[`` before that ``]`` fails the same way and the scan resumes there: a run
    of unclosed ``[[`` is read once, not once per opener.
    """
    pieces: list[str] = []
    copied = 0  # text before this index is already in ``pieces``
    search_from = 0
    while (start := text.find("[[", search_from)) != -1:
        target_start = start + 2
        close = text.find("]", target_start)
        if close == -1:
            break
        if close == target_start or not text.startswith("]", close + 1):
            search_from = close
            continue
        bar = text.find("|", target_start, close)
        # The display after the first "|", unless it is empty.
        shown_start = bar + 1 if 0 <= bar < close - 1 else target_start
        pieces.append(text[copied:start])
        pieces.append(text[shown_start:close])
        copied = search_from = close + 2
    if not pieces:
        return text
    pieces.append(text[copied:])
    return "".join(pieces)


def clean_heading_text(raw_heading: str) -> str:
    """Strip markdown formatting (links, bold, code, tags) to produce a pure heading title."""
    text = raw_heading.strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    # Strip wikilinks: [[target|display]] -> display, [[target]] -> target
    text = _strip_wikilinks(text)
    # Strip markdown links and images: [text](url) -> text, ![alt](url) -> alt
    text = replace_markdown_links(text, lambda label, _target, _original: label, image=None, label_min=0)
    # Strip inline code: `code` -> code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Strip bold and italics: **text**, __text__, *text*, _text_
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    # Strip strikethrough: ~~text~~ -> text
    text = re.sub(r"~~([^~]+)~~", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()
