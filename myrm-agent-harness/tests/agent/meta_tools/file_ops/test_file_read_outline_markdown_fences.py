"""The Markdown outline follows CommonMark code fences.

A block ends only at a fence of the same character that is at least as long and has nothing after it, and a read that
starts inside a block still lists the headings after it. Reading every fence-looking line as a toggle turned the
document inside out after a traceback's ``~~~~^^^^`` line: headings vanished and code comments were listed.
"""

from myrm_agent_harness.agent.meta_tools.file_ops.core.file_read.outline import (
    extract_file_outline,
    extract_truncated_outline,
)

_TICKS = "`" * 3


def _headings(*lines: str) -> list[str]:
    """What the outline of a whole Markdown document lists, without its header line."""
    return extract_file_outline("\n".join(lines), "doc.md").splitlines()[1:]


def _remaining(*lines: str, next_offset: int) -> list[str]:
    """What the outline of a read cut before line ``next_offset`` lists, with its header line."""
    return extract_truncated_outline("\n".join(lines), "doc.md", next_offset=next_offset).splitlines()


def test_a_traceback_pointer_line_in_a_backtick_block_does_not_end_the_block() -> None:
    out = _headings(
        "# Title", "", f"{_TICKS}python", "Traceback (most recent call last):", "    ~~~~^^^^", _TICKS, "", "## After"
    )

    assert out == ["  - Line 1: # Title", "  - Line 8: ## After"]


def test_a_pointer_line_in_a_tilde_block_does_not_end_the_block() -> None:
    out = _headings("# Title", "", "~~~", "code", "~~~~^^^^", "~~~", "", "## After")

    assert out == ["  - Line 1: # Title", "  - Line 8: ## After"]


def test_a_shorter_fence_quoted_in_a_longer_one_stays_code() -> None:
    long_fence = "`" * 4

    out = _headings(
        "# Title",
        "",
        f"{long_fence}markdown",
        _TICKS,
        "# a heading in the quoted example",
        _TICKS,
        long_fence,
        "",
        "## After",
    )

    assert out == ["  - Line 1: # Title", "  - Line 9: ## After"]


def test_a_fence_of_the_other_character_does_not_end_a_block() -> None:
    out = _headings("# Title", "", f"{_TICKS}text", "~~~", "# inside", _TICKS, "", "## After")

    assert out == ["  - Line 1: # Title", "  - Line 8: ## After"]


def test_a_fence_line_with_text_after_it_does_not_end_a_block() -> None:
    out = _headings("# Title", "", f"{_TICKS}text", f"{_TICKS}python", "# inside", _TICKS, "", "## After")

    assert out == ["  - Line 1: # Title", "  - Line 8: ## After"]


def test_a_closing_fence_may_be_longer_than_the_opening_one() -> None:
    out = _headings("# Title", "", f"{_TICKS}text", "# inside", "`" * 5, "", "## After")

    assert out == ["  - Line 1: # Title", "  - Line 7: ## After"]


def test_three_backticks_around_a_word_on_one_line_are_inline_code_not_a_fence() -> None:
    out = _headings("# Title", "", f"{_TICKS}inline{_TICKS}", "", "## After")

    assert out == ["  - Line 1: # Title", "  - Line 5: ## After"]


def test_two_marker_characters_are_strikethrough_or_inline_code_not_a_fence() -> None:
    out = _headings("# Title", "", "~~struck~~ words", "``code`` words", "", "## After")

    assert out == ["  - Line 1: # Title", "  - Line 6: ## After"]


def test_a_fence_that_is_never_closed_runs_to_the_end_of_the_document() -> None:
    out = _headings("# Title", "", f"{_TICKS}text", "## inside, as far as the document says")

    assert out == ["  - Line 1: # Title"]


def test_a_read_cut_inside_a_block_lists_the_headings_after_it() -> None:
    out = _remaining(
        "# Title",
        "",
        "## Before",
        "",
        f"{_TICKS}python",
        "x = 1",
        "y = 2",
        "z = 3",
        _TICKS,
        "",
        "## After",
        "",
        "## Last",
        next_offset=8,
    )

    assert out == ["[OUTLINE OF REMAINING SYMBOLS (from line 8)]:", "  - Line 11: ## After", "  - Line 13: ## Last"]


def test_a_read_cut_inside_a_block_does_not_list_its_comments() -> None:
    out = _remaining("# Title", f"{_TICKS}python", "# comment one", "# comment two", _TICKS, "## After", next_offset=3)

    assert out == ["[OUTLINE OF REMAINING SYMBOLS (from line 3)]:", "  - Line 6: ## After"]


def test_a_read_cut_inside_the_frontmatter_does_not_list_its_comments() -> None:
    out = _remaining("---", "title: T", "# a yaml comment", "author: A", "---", "", "# Real", next_offset=3)

    assert out == ["[OUTLINE OF REMAINING SYMBOLS (from line 3)]:", "  - Line 7: # Real"]


def test_the_gutter_of_a_read_does_not_hide_a_fence() -> None:
    lines = ["# Title", "", f"{_TICKS}python", "    ~~~~^^^^", _TICKS, "", "## After"]
    view = "\n".join(f"{number:6}|{line}" for number, line in enumerate(lines, start=1))

    assert extract_file_outline(view, "doc.md") == extract_file_outline("\n".join(lines), "doc.md")
    assert extract_file_outline(view, "doc.md").splitlines()[1:] == ["  - Line 1: # Title", "  - Line 7: ## After"]
