"""The comparable body text of a raw file: only a leading frontmatter block is metadata, a horizontal rule is content."""

from __future__ import annotations

import re
import time
from html import unescape
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.wiki.pipeline.corpus_dedup.fingerprint import (
    build_fingerprint,
    compute_normalized_body_hash,
    compute_simhash,
    extract_body_text,
)

_FRONTMATTER = "---\ntitle: Notes\ntags: [a, b]\n---\n"
_SECTIONS = "Intro.\n\n---\n\nFirst chapter about apples.\n\n---\n\nOutro.\n"


@pytest.mark.parametrize("bom", ["", "\ufeff"], ids=["plain", "bom"])
def test_the_leading_frontmatter_block_is_not_part_of_the_body(bom: str) -> None:
    body = extract_body_text(f"{bom}{_FRONTMATTER}{_SECTIONS}")

    assert "title: Notes" not in body
    assert body == _SECTIONS


def test_text_between_two_horizontal_rules_stays_in_the_body() -> None:
    body = extract_body_text(_SECTIONS)

    assert body == _SECTIONS
    assert "apples" in extract_body_text(f"{_FRONTMATTER}{_SECTIONS}")


def test_documents_that_differ_between_two_horizontal_rules_fingerprint_differently() -> None:
    apples = _SECTIONS
    oranges = _SECTIONS.replace("apples", "oranges, which are another fruit entirely")

    assert compute_normalized_body_hash(apples) != compute_normalized_body_hash(oranges)
    assert compute_simhash(apples) != compute_simhash(oranges)


def test_a_document_without_frontmatter_is_compared_as_it_is() -> None:
    assert extract_body_text("# Title\n\nPlain body.") == "# Title\n\nPlain body."


@pytest.mark.parametrize(
    "markup",
    [
        "<p>Hello <b>world</b> &amp; more</p>",
        "# Title\n\n<div>raw <i>html</i> in markdown</div>\n\n> a quote",
        "if a < b and c > d then <br/> stop",
        "<><p>after an empty tag</p>",
        "closed <i>tag</i> then <unterminated",
        "<p>one</p> tail <",
        "<b>&lt;escaped&gt;</b> text with a > sign",
        "<<<>>>",
    ],
)
def test_markup_is_reduced_to_text_exactly_as_the_plain_tag_pattern_would(markup: str) -> None:
    # Fingerprints of stored files are cached by FINGERPRINT_ALGORITHM_VERSION, so a body that reads differently here
    # would silently regroup duplicates; the expected value is the original pattern applied to the same text.
    assert extract_body_text(markup) == unescape(re.sub(r"<[^>]+>", " ", markup))


def test_text_without_a_complete_pair_of_brackets_is_left_alone() -> None:
    assert extract_body_text("1 < 2 &amp; 3") == "1 < 2 &amp; 3"
    assert extract_body_text("3 > 2 &amp; 1") == "3 > 2 &amp; 1"


class TestHostileFiles:
    """A run of "<" after any ">" is what makes the plain tag pattern rescan the rest of the file from every one."""

    _RUN = "x>" + "<" * 200_000 + " tail"

    def test_the_body_of_a_run_of_unterminated_tags_is_read_in_linear_time(self) -> None:
        # ~20 s with the quadratic pattern and the interpreter lock held; far above the linear cost, far below that.
        started = time.perf_counter()

        body = extract_body_text(self._RUN)

        assert time.perf_counter() - started < 2.0
        assert body == self._RUN

    def test_a_file_of_unterminated_tags_is_fingerprinted_in_linear_time(self, tmp_path: Path) -> None:
        raw = tmp_path / "hostile.md"
        raw.write_text(self._RUN, encoding="utf-8")
        started = time.perf_counter()

        fingerprint = build_fingerprint(raw, relative_path="hostile.md")

        assert time.perf_counter() - started < 2.0
        assert fingerprint.size_bytes == raw.stat().st_size
