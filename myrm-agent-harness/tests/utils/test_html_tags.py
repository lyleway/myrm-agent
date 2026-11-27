"""strip_tags: the result of the plain tag pattern, in time linear in the text."""

from __future__ import annotations

import itertools
import random
import re

import pytest

from myrm_agent_harness.utils.html_tags import strip_tags

# The pattern strip_tags reproduces. It defines the expected result for every input small enough to run it on.
_TAG_RE = re.compile(r"<[^>]+>")

_PAGE_SIZE = 750_000


class TestExamples:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("<p>a</p><b>b</b>", "ab"),
            ('<a href="x\ny">link</a>', "link"),
            ("a<>b", "a<>b"),
            ("a < b", "a < b"),
            ("a<<b>c", "ac"),
            ("1 < 2 and 3 > 2", "1  2"),
            ("<p>one</p> tail <", "one tail <"),
            ("", ""),
        ],
    )
    def test_closed_tags_go_and_everything_else_stays(self, text: str, expected: str) -> None:
        assert strip_tags(text) == expected

    def test_each_tag_becomes_the_replacement(self) -> None:
        assert strip_tags("a<b>c<d>e", replacement=" ") == "a c e"

    @pytest.mark.parametrize("replacement", ["\\", "\\1", "\\g<0>", "\\\\", "$&", "é"])
    def test_the_replacement_goes_in_as_written(self, replacement: str) -> None:
        assert strip_tags("a<b>c", replacement=replacement) == f"a{replacement}c"


class TestEquivalenceWithThePattern:
    _TOKENS = ("<", ">", "<>", "<<", ">>", "</", "<p>", "<a href='x'>", "a=b", "x", " ", "\n", "é", "&lt;", "hello ")

    def test_every_short_string_over_the_markup_alphabet(self) -> None:
        for length in range(9):
            for chars in itertools.product("<>a ", repeat=length):
                text = "".join(chars)

                assert strip_tags(text) == _TAG_RE.sub("", text), text
                assert strip_tags(text, replacement=" ") == _TAG_RE.sub(" ", text), text

    def test_random_markup(self) -> None:
        rnd = random.Random(2026)
        for _ in range(20_000):
            text = "".join(rnd.choice(self._TOKENS) for _ in range(rnd.randint(0, 24)))

            assert strip_tags(text) == _TAG_RE.sub("", text), text
            assert strip_tags(text, replacement=" ") == _TAG_RE.sub(" ", text), text


@pytest.mark.timeout(10)
class TestHostileText:
    """Page-size inputs on which the plain pattern rescans the rest of the text from every "<"."""

    def test_angle_brackets_without_a_tag_end(self) -> None:
        text = "<" * _PAGE_SIZE

        assert strip_tags(text) == text

    def test_angle_brackets_after_the_only_tag(self) -> None:
        assert strip_tags("<p>" + "<" * _PAGE_SIZE, replacement=" ") == " " + "<" * _PAGE_SIZE

    def test_angle_brackets_after_a_bracket_that_closes_nothing(self) -> None:
        text = "a>" + "<" * _PAGE_SIZE

        assert strip_tags(text) == text

    def test_openers_closed_only_by_one_distant_bracket_are_one_tag(self) -> None:
        assert strip_tags("<a" * (_PAGE_SIZE // 2) + ">tail") == "tail"

    def test_a_run_closed_by_a_bracket_and_a_second_run_that_is_not(self) -> None:
        assert strip_tags("<" * _PAGE_SIZE + ">" + "<" * _PAGE_SIZE) == "<" * _PAGE_SIZE

    def test_empty_tags_stay_and_cost_one_visit_each(self) -> None:
        text = "<>" * (_PAGE_SIZE // 2)

        assert strip_tags(text) == text

    def test_a_page_of_ordinary_tags(self) -> None:
        assert strip_tags("<p>word</p>" * (_PAGE_SIZE // 11)) == "word" * (_PAGE_SIZE // 11)
