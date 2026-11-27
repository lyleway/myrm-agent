"""Markdown link scans whose cost grows linearly with the text, however broken the brackets are.

[INPUT]
- (none)

[OUTPUT]
- LinkSpan: Where a link sits in its text (start, label_start, label_end, end)
- markdown_links: Each ``[label](target)`` of a text, as ``\\[([^\\]]+)\\]\\(([^)]+)\\)`` finds it; the shape (image, empty label or target, rejected target characters) is chosen per call
- replace_markdown_links: The text with each such link replaced by what a renderer returns for it
- mask_unclosable_openers: The text with each "[" that no link can start at replaced by NUL, for a lazy-label pattern that must not try them
- nested_markdown_links: Each ``[label](target)`` whose label may hold brackets, as ``(?<!\\!)\\[(.+?)\\]\\(([^)]+)\\)`` finds it
- replace_nested_markdown_links: The text with each such link replaced by what a renderer returns for it

[POS]
The one scan for Markdown links in text that can come from a remote page, a document, a model or a user. A pattern
such as ``\\[([^\\]]+)\\]\\(([^)]+)\\)`` tries every "[" against the whole rest of the text, so a run of "[" with no
closer after it costs time that grows with the square of its length (20,000 of them: about 0.9 s with the interpreter
lock held), and the lazy ``\\[.+?\\]\\(.+?\\)`` the cube (4,000 characters of ``[a](``: about 4.5 s). The scans here
return the links those patterns match, and ``replace_*`` splices them like ``re.sub``, in time proportional to the
length.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from functools import cache
from typing import NamedTuple

LinkRenderer = Callable[[str, str, str], str]
"""``render(label, target, original)``: the replacement for a link; ``original`` is the link as written."""

_SHORTEST_LINK = 5  # the ")" of ``[a](b)``, the shortest link, sits at this index
_NESTED_LINK_RE = re.compile(r"(?<!\!)\[(.+?)\]\(([^)]+)\)", re.DOTALL)


class LinkSpan(NamedTuple):
    """Where a link sits in its text."""

    start: int  # the first character of the link: the "!" of an image taken as one, else the "["
    label_start: int  # the first character of the label, just past the "["
    label_end: int  # the "]" that closes the label
    end: int  # one past the last character: the ")", or the "(" when the link has no target part

    def label(self, text: str) -> str:
        return text[self.label_start : self.label_end]

    def target(self, text: str) -> str:
        """The target of a link that has a target part (not a ``target_min=None`` presence match)."""
        return text[self.label_end + 2 : self.end - 1]

    def original(self, text: str) -> str:
        return text[self.start : self.end]


@cache
def _target_stop(rejected: str) -> re.Pattern[str]:
    """What ends a target: the first ``)``, or the first of ``rejected`` (a target must not hold one)."""
    return re.compile("[" + re.escape(")" + rejected) + "]")


def markdown_links(
    text: str,
    *,
    image: bool | None = False,
    label_min: int = 1,
    target_min: int | None = 1,
    reject_in_target: str = "",
) -> Iterator[LinkSpan]:
    """The links of ``text`` that ``\\[([^\\]]{label_min,})\\]\\(([^)]{target_min,})\\)`` finds, in order.

    The label runs to the first ``]`` after the ``[`` and must be followed by ``(``; the target runs to the first
    ``)`` after it. A link that cannot close consumes nothing but its own ``]``, which every opener before it reads as
    its label's end too, so the scan resumes after that ``]`` and visits each character a bounded number of times.

    Args:
        text: The text to scan.
        image: ``True`` for ``![label](target)`` only (a link whose ``[`` follows a ``!`` is then no image's); ``False``
            for every ``[label](target)``, a preceding ``!`` staying outside it; ``None`` for either, a ``!`` right
            before the ``[`` belonging to the link (``!?\\[...``).
        label_min: The fewest characters in the label (0 allows ``[](target)``).
        target_min: The fewest characters in the target; ``None`` for a shape with no target part, where a link ends
            after ``](`` (``!\\[[^\\]]*\\]\\(`` finds the start of every image).
        reject_in_target: Characters a target must not hold; a "link" whose target runs into one is not a link.
    """
    opener = "![" if image else "["
    stop_pattern = _target_stop(reject_in_target)
    position = 0  # where the next opener is looked for
    stop: re.Match[str] | None = None  # the first ")" or rejected character found at or after an earlier target start
    while (start := text.find(opener, position)) != -1:
        label_start = start + len(opener)
        label_end = text.find("]", label_start)
        if label_end == -1:
            return  # no "]" ahead: no later opener has one either
        if label_end - label_start < label_min or not text.startswith("(", label_end + 1):
            position = label_end  # every opener up to this "]" reads the same label and fails the same way
            continue
        if target_min is None:
            end = label_end + 2
        else:
            target_start = label_end + 2
            if stop is None or stop.start() < target_start:
                stop = stop_pattern.search(text, target_start)
                if stop is None:
                    return  # no ")" ahead: no later link closes either
            if stop.group() != ")" or stop.start() - target_start < target_min:
                position = label_end
                continue
            end = stop.end()
        # A link ends in ")" (or "("), so a "!" right before an opener is never part of the previous link.
        first = start - 1 if image is None and start > 0 and text[start - 1] == "!" else start
        yield LinkSpan(first, label_start, label_end, end)
        position = end


def mask_unclosable_openers(text: str, *, lazy_target: bool = False) -> str:
    """``text`` with every ``[`` that no link can start at replaced by NUL, so a lazy-label pattern never tries one.

    A label ``\\[.+?\\]\\(`` runs to the first ``](`` at least two characters after its opener whose target closes,
    and whether a ``](`` closes does not depend on the opener before it. So an opener has a link exactly when a
    closing ``](`` lies ahead, and the openers after the last closing ``](`` each read the rest of the text to fail
    (``[a](`` repeated 1,000 times: about 4.5 s). They are replaced by NUL, which a pattern reads like any character
    that is not special to it, so the matches are the same and cost the length of what they consume. Match the result
    and take the matched text from ``text``: the offsets are the same.

    Args:
        text: One line, or text whose pattern's ``.`` reads line breaks too.
        lazy_target: ``True`` when the target is ``.+?\\)`` (a ``](`` closes when a ``)`` follows its ``(``); ``False``
            for ``[^)]+\\)`` (the first ``)`` after the ``(`` must not come right behind it).
    """
    if "[" not in text:
        return text
    paren = text.rfind(")")
    # A closing ``](`` is followed by a character other than the ")" that ends it, and a ")" at least one more ahead.
    closer = text.rfind("](", 0, paren - 1) if paren >= _SHORTEST_LINK else -1
    while not lazy_target and closer != -1 and text[closer + 2] == ")":
        closer = text.rfind("](", 0, closer + 1)
    keep = max(closer - 1, 0)  # the openers before this index have a closing ``](`` at least two characters ahead
    tail = text[keep:]
    if "[" not in tail:
        return text
    return text[:keep] + tail.replace("[", "\x00")


def nested_markdown_links(text: str) -> Iterator[LinkSpan]:
    """The links, images apart, that ``(?<!\\!)\\[(.+?)\\]\\(([^)]+)\\)`` finds in ``text`` with ``.`` reading line breaks.

    The label is the shortest run that ends at a ``](`` whose target closes, so it may hold ``[``, ``]`` and ``(``.
    """
    for match in _NESTED_LINK_RE.finditer(mask_unclosable_openers(text)):
        yield LinkSpan(match.start(), match.start(1), match.end(1), match.end())


def _splice(text: str, links: Iterator[LinkSpan], render: LinkRenderer) -> str:
    """``text`` with each link replaced by ``render(label, target, original)``."""
    pieces: list[str] = []
    last_end = 0
    for link in links:
        pieces.append(text[last_end : link.start])
        pieces.append(render(link.label(text), link.target(text), link.original(text)))
        last_end = link.end
    if not pieces:
        return text
    pieces.append(text[last_end:])
    return "".join(pieces)


def replace_markdown_links(
    text: str,
    render: LinkRenderer,
    *,
    image: bool | None = False,
    label_min: int = 1,
    target_min: int = 1,
    reject_in_target: str = "",
) -> str:
    """``text`` with each link of ``markdown_links`` replaced by ``render(label, target, original)``.

    The result is what ``re.sub`` gives with the same pattern and a replacement function: the pieces between links
    stay, and what ``render`` returns is inserted as written.
    """
    return _splice(
        text,
        markdown_links(
            text, image=image, label_min=label_min, target_min=target_min, reject_in_target=reject_in_target
        ),
        render,
    )


def replace_nested_markdown_links(text: str, render: LinkRenderer) -> str:
    """``text`` with each link of ``nested_markdown_links`` replaced by ``render(label, target, original)``."""
    return _splice(text, nested_markdown_links(text), render)
