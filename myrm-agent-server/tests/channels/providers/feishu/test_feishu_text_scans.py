"""The Feishu Markdown scans read what the patterns they replace read, in time proportional to the message.

The old patterns are the reference here, not the code under test. The lazy link pattern costs the cube of the line when
``[`` openers have no closer (1,000 repeats of ``[a](``: 4.6 s with the interpreter lock held, so the whole server waits),
and the italic-underscore pattern the square.
"""

from __future__ import annotations

import itertools
import random
import re
import time
from collections.abc import Callable, Iterator

import pytest

from app.channels.providers.feishu.cards import build_post_content, has_rich_text
from app.channels.providers.feishu.cards.text import _bolded, _has_underscore_emphasis, _is_word_char, _parse_post_line

_OLD_RICH_TEXT_RE = re.compile(r"\*\*.+?\*\*" r"|(?<!\w)_.+?_(?!\w)" r"|\[.+?\]\(.+?\)" r"|~~.+?~~" r"|`[^`]+`")
_OLD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_OLD_POST_INLINE_RE = re.compile(r"(\*\*(.+?)\*\*)" r"|(~~(.+?)~~)" r"|(`([^`]+)`)" r"|(\[(.+?)\]\((.+?)\))")
_OLD_UNDERSCORE_RE = re.compile(r"(?<!\w)_.+?_(?!\w)")


def _old_has_rich_text(content: str) -> bool:
    return bool(_OLD_RICH_TEXT_RE.search(content) or _OLD_HEADING_RE.search(content))


def _old_parse_post_line(line: str) -> list[dict[str, object]]:
    elements: list[dict[str, object]] = []
    pos = 0
    for m in _OLD_POST_INLINE_RE.finditer(line):
        if m.start() > pos:
            elements.append({"tag": "text", "text": line[pos : m.start()]})
        if m.group(2) is not None:
            elements.append({"tag": "text", "text": m.group(2), "style": ["bold"]})
        elif m.group(4) is not None:
            elements.append({"tag": "text", "text": m.group(4), "style": ["lineThrough"]})
        elif m.group(6) is not None:
            elements.append({"tag": "text", "text": m.group(0)})
        elif m.group(8) is not None:
            elements.append({"tag": "a", "text": m.group(8), "href": m.group(9)})
        pos = m.end()
    if pos < len(line):
        elements.append({"tag": "text", "text": line[pos:]})
    return elements or [{"tag": "text", "text": line}]


def _old_post_paragraphs(content: str) -> list[list[dict[str, object]]]:
    paragraphs: list[list[dict[str, object]]] = []
    for line in content.split("\n"):
        heading_match = _OLD_HEADING_RE.match(line)
        if heading_match:
            paragraphs.append([_bolded(element) for element in _old_parse_post_line(line[heading_match.end() :])])
        else:
            paragraphs.append(_old_parse_post_line(line))
    return paragraphs


_PIECES = (
    "[", "]", "(", ")", "a", " ", "_", "*", "**", "~~", "`", "\n", "é", "٣", "一", "²", "-", "#",
    "[a](b)", "**b**", "_i_", "snake_case", "](", "[a]()", "~~x~~", "`c`", "## ",
)  # fmt: skip


def _every_string(alphabet: tuple[str, ...], longest: int) -> Iterator[str]:
    for length in range(longest + 1):
        for letters in itertools.product(alphabet, repeat=length):
            yield "".join(letters)


def _random_strings(count: int, longest: int, seed: int, pieces: tuple[str, ...] = _PIECES) -> Iterator[str]:
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(pieces) for _ in range(rng.randint(0, longest)))


class TestReadsWhatTheOldPatternsRead:
    def test_rich_text_on_every_short_text(self) -> None:
        for text in _every_string(("[", "]", "(", ")", "_", "a", "\n"), 6):
            assert has_rich_text(text) == _old_has_rich_text(text), repr(text)

    def test_rich_text_on_every_short_text_with_the_other_delimiters(self) -> None:
        for text in _every_string(("**", "~~", "`", "_", "a", " ", "#"), 5):
            assert has_rich_text(text) == _old_has_rich_text(text), repr(text)

    def test_rich_text_on_random_text(self) -> None:
        for text in _random_strings(30_000, 14, seed=3):
            assert has_rich_text(text) == _old_has_rich_text(text), repr(text)

    def test_post_line_on_every_short_line(self) -> None:
        for text in _every_string(("[", "]", "(", ")", "*", "a", "`"), 6):
            assert _parse_post_line(text) == _old_parse_post_line(text), repr(text)

    def test_post_line_on_random_lines(self) -> None:
        for text in _random_strings(30_000, 14, seed=5):
            line = text.replace("\n", " ")
            assert _parse_post_line(line) == _old_parse_post_line(line), repr(line)

    def test_post_payload_on_random_messages(self) -> None:
        for text in _random_strings(10_000, 20, seed=9):
            assert build_post_content(text) == {"zh_cn": {"title": "", "content": _old_post_paragraphs(text)}}, repr(text)

    def test_underscore_emphasis_on_every_short_text_over_word_and_other_characters(self) -> None:
        for text in _every_string(("_", "a", "٣", "é", " ", "-", "²"), 6):
            assert _has_underscore_emphasis(text) == bool(_OLD_UNDERSCORE_RE.search(text)), repr(text)

    def test_a_word_character_is_what_the_pattern_calls_one(self) -> None:
        word = re.compile(r"\w")
        for code in itertools.chain(
            range(0x250), range(0x370, 0x500), range(0x2000, 0x2200), range(0x3000, 0x3100), range(0x4E00, 0x4E40)
        ):
            char = chr(code)
            assert _is_word_char(char) == bool(word.fullmatch(char)), hex(code)


class TestExamples:
    @pytest.mark.parametrize(
        ("content", "rich"),
        [
            ("see [docs](https://x.dev)", True),
            ("see [docs](", False),  # a link that never closes is plain text
            ("[a](b", False),
            ("[a] (b)", False),
            ("[a]()", False),
            ("[[a](b)", True),  # the label may hold a bracket
            ("first\n[a](\nthird)", False),  # a link does not run across lines
            ("_italic_", True),
            ("a _italic_ word", True),
            ("snake_case_name", False),
            ("x _a", False),
            ("é _a_", True),
            ("é_a_", False),  # the underscore sits behind a word character
            ("٣_a_", False),
            ("_a_b", False),  # the closing underscore is followed by a word character
            ("_a_ b_", True),
            ("**bold**", True),
            ("~~gone~~", True),
            ("`code`", True),
        ],
    )
    def test_has_rich_text(self, content: str, rich: bool) -> None:
        assert has_rich_text(content) is rich
        assert _old_has_rich_text(content) is rich

    def test_a_link_after_text_that_looks_like_an_unclosed_one_is_still_a_link(self) -> None:
        assert _parse_post_line("[a [b](c)") == [
            {"tag": "a", "text": "a [b", "href": "c"},
        ]

    def test_openers_beyond_the_last_closing_pair_stay_text(self) -> None:
        assert _parse_post_line("[a](b) and [c](") == [
            {"tag": "a", "text": "a", "href": "b"},
            {"tag": "text", "text": " and [c]("},
        ]

    def test_a_link_inside_bold_text_is_part_of_the_bold_span(self) -> None:
        assert _parse_post_line("**x [a](b) y**") == [{"tag": "text", "text": "x [a](b) y", "style": ["bold"]}]


_HOSTILE: dict[str, Callable[[int], str]] = {
    "link-without-close": lambda n: "[a](" * (n // 4),
    "link-closing-at-the-end": lambda n: "[a](" * (n // 4) + ")",
    "openers": lambda n: "[" * n,
    "underscore-words": lambda n: " _a" * (n // 3),
    "underscores": lambda n: "_" * n,
    "bold-then-openers": lambda n: "**" + "[a](" * (n // 4),
    "mixed": lambda n: " _a[b](" * (n // 7),
    "lines-of-openers": lambda n: "[a](\n" * (n // 5),
    "empty-targets": lambda n: "[a]()" * (n // 5),
    "code-and-openers": lambda n: "`" + "[a](" * (n // 4),
}


def _read_in(text: str) -> float:
    """The least CPU seconds of two reads of ``text`` by each public entry point."""
    best = float("inf")
    for _ in range(2):
        started = time.process_time()
        has_rich_text(text)
        build_post_content(text)
        best = min(best, time.process_time() - started)
    return best


class TestLinearTime:
    # The text grows eightfold: linear time grows about eightfold, quadratic about sixty-four-fold, cubic more.
    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_message(self, family: str) -> None:
        short = _read_in(_HOSTILE[family](50_000))
        assert short < 1.0

        long = _read_in(_HOSTILE[family](400_000))

        assert long < 24 * max(short, 0.001)
