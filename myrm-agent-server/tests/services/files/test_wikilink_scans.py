"""The workspace wikilink rewrite reads the links its pattern read, in time proportional to the note.

The old pattern is the reference here, not the code under test. A target, an anchor and an alias each stop at a ``]``,
and the pattern reads up to it from every ``[[`` that does not close there: a note of 4,000 ``[`` took half a second
and one of 8,000 over two.
"""

from __future__ import annotations

import itertools
import random
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from app.services.files.organize.wikilink import _rewrite_text, _wikilinks, rewrite_wikilinks_in_tree

_OLD_RE = re.compile(r"\[\[([^\]|#]+)(#[^\]|]+)?(\|[^\]]+)?\]\]")


def _old_links(text: str) -> list[tuple[int, int, str, str]]:
    return [(m.start(), m.end(), m.group(1), (m.group(2) or "") + (m.group(3) or "")) for m in _OLD_RE.finditer(text)]


def _links(text: str) -> list[tuple[int, int, str, str]]:
    return [(start, end, text[start + 2 : target_end], text[target_end : end - 2]) for start, target_end, end in _wikilinks(text)]


def _old_rewrite_text(text: str, path_map: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        mapped = path_map.get(target)
        if mapped is None:
            mapped = path_map.get(Path(target).stem)
        if mapped is None:
            return match.group(0)
        suffix = (match.group(2) or "") + (match.group(3) or "")
        display = Path(mapped).stem if "/" not in mapped and "\\" not in mapped else mapped
        return f"[[{display}{suffix}]]"

    return _OLD_RE.sub(repl, text)


_PIECES = (
    "[[", "]]", "[", "]", "|", "#", "a", "b c", " ", "\n", "é", "[[a]]", "[[a#h|b]]", "[[ a ]]", "[[a#h]]", "[[a|b]]",
)  # fmt: skip
_MAP = {"a.md": "renamed.md", "a": "renamed", "dir/c.md": "other/c.md", "c": "c", "b c": "d"}


def _random_strings(count: int, longest: int, seed: int) -> Iterator[str]:
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(_PIECES) for _ in range(rng.randint(0, longest)))


class TestFindsWhatThePatternFound:
    @pytest.mark.parametrize("length", range(9))
    def test_every_text_of_that_length(self, length: int) -> None:
        for letters in itertools.product("[]|#a", repeat=length):
            text = "".join(letters)
            assert _links(text) == _old_links(text), text

    def test_random_text(self) -> None:
        for text in _random_strings(40_000, 16, seed=51):
            assert _links(text) == _old_links(text), repr(text)

    @pytest.mark.parametrize(
        ("text", "links"),
        [
            ("[[a]]", [(0, 5, "a", "")]),
            ("[[a#h]]", [(0, 7, "a", "#h")]),
            ("[[a|alias]]", [(0, 11, "a", "|alias")]),
            ("[[a#h|alias]]", [(0, 13, "a", "#h|alias")]),
            ("[[a|b#c|d]]", [(0, 11, "a", "|b#c|d")]),  # an alias may hold "#" and "|"
            ("[[a#b#c]]", [(0, 9, "a", "#b#c")]),  # so may an anchor, but not "|"
            ("[[a#]]", []),  # an anchor is not empty
            ("[[a#|b]]", []),
            ("[[a|]]", []),  # nor is an alias
            ("[[|a]]", []),  # nor a target
            ("[[#a]]", []),
            ("[[[a]]", [(0, 6, "[a", "")]),  # a target may hold "["
            ("[[a]x]]", []),  # a link closes at the first "]" or not at all
            ("[[a]] [[b]]", [(0, 5, "a", ""), (6, 11, "b", "")]),
            ("[[a#|b]] [[c]]", [(9, 14, "c", "")]),  # a failed link leaves the ones after it
        ],
    )
    def test_examples(self, text: str, links: list[tuple[int, int, str, str]]) -> None:
        assert _links(text) == links


class TestRewrite:
    def test_rewrites_what_the_pattern_rewrote(self) -> None:
        for text in _random_strings(30_000, 14, seed=52):
            assert _rewrite_text(text, _MAP) == _old_rewrite_text(text, _MAP), repr(text)

    @pytest.mark.parametrize(
        ("text", "rewritten"),
        [
            ("see [[a]] and [[a#h|x]]", "see [[renamed]] and [[renamed#h|x]]"),
            ("[[ a ]]", "[[renamed]]"),  # the target is read without the spaces around it
            ("[[a.md]]", "[[renamed]]"),
            ("[[dir/c.md|text]]", "[[other/c.md|text]]"),  # a path keeps its folders
            ("[[unknown]] [[a]]", "[[unknown]] [[renamed]]"),
            ("[[a#|b]] [[a]]", "[[a#|b]] [[renamed]]"),
        ],
    )
    def test_examples(self, text: str, rewritten: str) -> None:
        assert _rewrite_text(text, _MAP) == rewritten

    def test_a_note_of_openers_does_not_hold_up_the_rewrite_of_the_others(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "a.md").write_text("see [[b]]", encoding="utf-8")
        (root / "b.md").write_text("# B", encoding="utf-8")
        (root / "flood.md").write_text("[" * 12_000 + "#]]\n[[b]]", encoding="utf-8")

        started = time.process_time()
        updated = rewrite_wikilinks_in_tree(str(root), [(str(root / "b.md"), str(root / "docs" / "c.md"))])

        assert updated == 2
        assert (root / "a.md").read_text(encoding="utf-8") == "see [[c]]"
        assert (root / "flood.md").read_text(encoding="utf-8") == "[" * 12_000 + "#]]\n[[c]]"
        assert time.process_time() - started < 1.0


_HOSTILE: dict[str, Callable[[int], str]] = {
    "openers": lambda n: "[[" * (n // 2),
    "single-openers": lambda n: "[" * n,
    "long-target-empty-anchor": lambda n: "[" * n + "#]]",
    "long-target-empty-alias": lambda n: "[" * n + "|]]",
    "anchors": lambda n: "[[a#" * (n // 4),
    "aliases": lambda n: "[[a|" * (n // 4),
    "aliases-then-a-single-bracket": lambda n: "[[a|" * (n // 4) + "]x",
    "anchors-then-aliases": lambda n: "[[a#b|" * (n // 6),
    "anchors-then-a-single-bracket": lambda n: "[[a#b" * (n // 5) + "]x",
    "empty-parts": lambda n: "[[a#|" * (n // 5) + "]]",
    "links": lambda n: "[[a]] " * (n // 6),
}


def _rewrite_in(text: str) -> float:
    """The least CPU seconds of two rewrites of ``text``."""
    best = float("inf")
    for _ in range(2):
        started = time.process_time()
        _rewrite_text(text, _MAP)
        best = min(best, time.process_time() - started)
    return best


class TestLinearTime:
    # The note grows eightfold: linear time grows about eightfold, quadratic about sixty-four-fold.
    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_note(self, family: str) -> None:
        # The pattern takes seconds on the probe where the scan takes about a millisecond, and would take minutes on
        # the longer notes, so the probe has to fail the test before they are tried.
        assert _rewrite_in(_HOSTILE[family](8_000)) < 0.5

        short = _rewrite_in(_HOSTILE[family](50_000))
        assert short < 1.0

        long = _rewrite_in(_HOSTILE[family](400_000))

        assert long < 24 * max(short, 0.001)

    def test_openers_inside_the_target_of_a_failed_link_are_stepped_past(self) -> None:
        # Each of them would fail as the first one does. Trying every one again is linear too, the marks being
        # remembered, but takes a hundred times as long: 115 ms against 1.1 ms on this note.
        text = "[[[[" * 100_000 + "]"

        best = float("inf")
        for _ in range(3):
            started = time.process_time()
            assert list(_wikilinks(text)) == []
            best = min(best, time.process_time() - started)

        assert best < 0.025
