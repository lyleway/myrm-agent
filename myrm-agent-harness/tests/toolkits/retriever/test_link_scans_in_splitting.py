"""Splitting and quality assessment read hostile text in time proportional to its length.

The old patterns are the reference. The lazy link pattern took the cube of a line (4,000 characters of ``[a](``:
about 4.5 s), and ``^\\s*`` at every line start read a page of blank lines to its end once per line (20,000 of them:
about 1.4 s).
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable

import pytest

from myrm_agent_harness.toolkits.retriever.splitter.chunk_processor import (
    _LIST_ITEM_RE,
    _NUMBERED_ITEM_RE,
    assess_content_quality,
    calculate_text_metrics,
)
from myrm_agent_harness.toolkits.retriever.splitter.markdown_link_handler import MarkdownLinkHandler

_OLD_LINK_PATTERN = re.compile(r"(?<!\!)\[(.+?)\]\(([^)]+)\)", re.MULTILINE | re.DOTALL)
_PIECES = (
    "[", "]", "(", ")", "!", "a", " ", "\n", "\n\n", "](", "![", "[a](b)", "[long\nlink](https://x.y/z)", "![i](j)", "- ",
    "* ", "1. ", "12. ", "• ", "\t", "\r", "Sentence one. ", "Another one! ", "## ", "```", "é",
)  # fmt: skip


def _texts(count: int, longest: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(_PIECES) for _ in range(rng.randint(0, longest))) for _ in range(count)]


def _cpu_seconds(work: Callable[[], object]) -> float:
    """The least CPU time of two runs; the least leaves out scheduler noise."""
    best = float("inf")
    for _ in range(2):
        started = time.process_time()
        work()
        best = min(best, time.process_time() - started)
    return best


def _assert_linear(make: Callable[[int], str], work: Callable[[str], object], small: int = 50_000) -> None:
    """The text grows eightfold: linear time grows about eightfold, quadratic about sixty-four-fold."""
    # A cubic scan takes seconds on the probe where the linear one takes about a millisecond, and could not
    # return from the longer texts at all, so the probe has to fail the test before they are tried.
    assert _cpu_seconds(lambda: work(make(4_000))) < 0.5

    short = _cpu_seconds(lambda: work(make(small)))
    assert short < 1.0

    assert _cpu_seconds(lambda: work(make(small * 8))) < 24 * max(short, 0.001)


class TestLinkProtection:
    def test_protects_the_links_the_pattern_matched(self) -> None:
        handler = MarkdownLinkHandler()
        for text in _texts(10_000, 12, seed=41):
            protected, link_map = handler.protect_markdown_links(text)

            expected = [match.group(0) for match in _OLD_LINK_PATTERN.finditer(text) if "\n" in match.group(0)]
            assert list(link_map.values()) == expected, repr(text)
            assert handler.restore_markdown_links(protected, link_map) == text, repr(text)

    def test_a_link_split_by_a_line_break_is_replaced_by_a_placeholder_and_restored(self) -> None:
        handler = MarkdownLinkHandler()
        text = "See this [very long\nlink text](https://example.com) for details. And [short](https://a.test)."

        protected, link_map = handler.protect_markdown_links(text)

        assert "\n" not in protected
        assert protected.startswith("See this __MDLINK_0__")
        assert "[short](https://a.test)" in protected
        assert link_map == {0: "[very long\nlink text](https://example.com)"}
        assert handler.restore_markdown_links(protected, link_map) == text

    def test_an_image_is_not_a_link(self) -> None:
        handler = MarkdownLinkHandler()
        text = "![alt\ntext](https://example.com/a.png)"

        assert handler.protect_markdown_links(text) == (text, {})

    @pytest.mark.parametrize(
        "make",
        [
            lambda n: "[a](" * (n // 4),
            lambda n: "[" * n,
            lambda n: "[a](" * (n // 4) + ")",
            lambda n: "[a](\n" * (n // 5),
            lambda n: "[a]()" * (n // 5),
            lambda n: "![" * (n // 2),
        ],
        ids=[
            "links-without-close",
            "openers",
            "links-closing-at-the-end",
            "lines-of-links-without-close",
            "empty-targets",
            "image-openers",
        ],
    )
    def test_time_grows_in_proportion_to_the_text(self, make: Callable[[int], str]) -> None:
        handler = MarkdownLinkHandler()

        _assert_linear(make, handler.protect_markdown_links)


def _old_assess_content_quality(content: str) -> str:
    """``assess_content_quality`` as it was: the navigation check on every text, ``^\\s*`` list patterns."""
    if not content or not isinstance(content, str):
        return "low"

    content_stripped = content.strip()
    if len(content_stripped) < 30:
        return "low"

    junk_patterns = [
        r"^(View|Click|More|Details|Expand|Collapse|Login|Register|Search|Menu|Navigation)$",
        r"^\w+\s*>\s*\w+\s*>\s*\w+$",
        r"^\*{3,}$",
        r"^#{1,6}\s*$",
    ]
    for pattern in junk_patterns:
        if re.match(pattern, content_stripped, re.IGNORECASE):
            return "low"

    lines = content_stripped.split("\n")
    if len(lines) >= 3:
        link_lines = sum(1 for line in lines if re.search(r"\[.+?\]\(.+?\)", line.strip()))
        if link_lines / len(lines) > 0.9 and len(content_stripped) < 100:
            return "low"

    metrics = calculate_text_metrics(content_stripped)
    sentence_count = metrics["sentence_count"]
    content_length = metrics["length"]

    code_blocks = len(re.findall(r"```", content_stripped)) // 2
    list_items = len(re.findall(r"^\s*[-*+•]\s+", content_stripped, re.MULTILINE))
    numbered_items = len(re.findall(r"^\s*\d+\.\s+", content_stripped, re.MULTILINE))

    density_score = 0
    if content_length >= 150:
        density_score += 3
    elif content_length >= 60:
        density_score += 2
    elif content_length >= 30:
        density_score += 1
    if sentence_count >= 4:
        density_score += 3
    elif sentence_count >= 2:
        density_score += 2
    elif sentence_count >= 1:
        density_score += 1
    structure_items = code_blocks + list_items + numbered_items
    if structure_items >= 3:
        density_score += 2
    elif structure_items >= 1:
        density_score += 1
    return "high" if density_score >= 3 else "low"


class TestContentQuality:
    def test_assesses_what_the_old_code_assessed(self) -> None:
        for text in _texts(30_000, 24, seed=43):
            assert assess_content_quality(text) == _old_assess_content_quality(text), repr(text)

    def test_counts_the_list_items_the_old_patterns_counted(self) -> None:
        old_list = re.compile(r"^\s*[-*+•]\s+", re.MULTILINE)
        old_numbered = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
        for text in _texts(30_000, 16, seed=47):
            assert len(_LIST_ITEM_RE.findall(text)) == len(old_list.findall(text)), repr(text)
            assert len(_NUMBERED_ITEM_RE.findall(text)) == len(old_numbered.findall(text)), repr(text)

    def test_a_menu_of_links_is_low_quality_and_prose_is_high(self) -> None:
        assert assess_content_quality("[Home](/)\n[About](/about)\n[Contact](/contact)") == "low"
        prose = (
            "A first sentence about payments. A second one about gateways. A third about refunds. A fourth about fees."
        )
        assert assess_content_quality(prose) == "high"

    @pytest.mark.parametrize(
        "make",
        [
            lambda n: ("[a](" * (n // 12) + "\n") * 3,
            lambda n: "a" * 40 + "\n" * n + "b",
            lambda n: "a" * 40 + ("\n" + " " * 3) * (n // 4) + "b",
            lambda n: "- \n" * (n // 3),
            lambda n: "1.\n" * (n // 3),
        ],
        ids=[
            "lines-of-links-without-close",
            "blank-lines",
            "indented-blank-lines",
            "empty-list-items",
            "empty-numbered-items",
        ],
    )
    def test_time_grows_in_proportion_to_the_text(self, make: Callable[[int], str]) -> None:
        _assert_linear(make, assess_content_quality)
