"""The link scans find what the patterns they replace found, in time proportional to the text.

The patterns below are the ones the scans replace. They are the reference, not the code under test: backtracking from
every opener makes a run of unclosed ``[`` cost the square of its length (the lazy forms, the cube). The scans must
return the same spans on every short text over a small alphabet, on random longer ones, and take time in proportion to
the length of a hostile text.
"""

from __future__ import annotations

import itertools
import random
import re
import time
from collections.abc import Callable, Iterator

import pytest

from myrm_agent_harness.utils.markdown_links import (
    LinkSpan,
    markdown_links,
    mask_unclosable_openers,
    nested_markdown_links,
    replace_markdown_links,
    replace_nested_markdown_links,
)

# Every shape a caller in the codebase asks for, and the ones between them.
_STRICT_SHAPES = [
    pytest.param(
        image,
        label_min,
        target_min,
        reject,
        id=f"image={image}-label{label_min}-target{target_min}-reject{bool(reject)}",
    )
    for image in (False, True, None)
    for label_min in (0, 1)
    for target_min in (0, 1, None)
    for reject in ("", "\x00")
    if not (target_min is None and reject)
]
_NESTED_REFERENCE = re.compile(r"(?<!\!)\[(.+?)\]\(([^)]+)\)", re.DOTALL)


def _strict_reference(image: bool | None, label_min: int, target_min: int | None, reject: str) -> re.Pattern[str]:
    opener = {False: r"\[", True: r"!\[", None: r"!?\["}[image]
    label = rf"([^\]]{{{label_min},}})"
    if target_min is None:
        return re.compile(rf"{opener}{label}\]\(")
    return re.compile(rf"{opener}{label}\]\(([^){re.escape(reject)}]{{{target_min},}})\)")


def _spans(pattern: re.Pattern[str], text: str) -> list[LinkSpan]:
    return [LinkSpan(m.start(), m.start(1), m.end(1), m.end()) for m in pattern.finditer(text)]


def _every_string(alphabet: tuple[str, ...], longest: int) -> Iterator[str]:
    for length in range(longest + 1):
        for letters in itertools.product(alphabet, repeat=length):
            yield "".join(letters)


def _random_strings(pieces: tuple[str, ...], count: int, longest: int, seed: int) -> Iterator[str]:
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(pieces) for _ in range(rng.randint(0, longest)))


_STRICT_PIECES = ("[", "]", "(", ")", "a", " ", "](", "[]", "()", "![", "!", "\x00", "\n", "https://x.y", "**")
_NESTED_PIECES = ("[", "]", "(", ")", "a", " ", "](", "[]", "()", "![", "!", "\n", "[a](b)", "](c)")


def _strict_alphabet(image: bool | None, reject: str) -> tuple[str, ...]:
    return ("[", "]", "(", ")", "a") + (("!",) if image is not False else ()) + ((reject,) if reject else ())


class TestStrictScanFindsWhatThePatternFinds:
    @pytest.mark.parametrize(("image", "label_min", "target_min", "reject"), _STRICT_SHAPES)
    def test_every_short_text(self, image: bool | None, label_min: int, target_min: int | None, reject: str) -> None:
        pattern = _strict_reference(image, label_min, target_min, reject)
        alphabet = _strict_alphabet(image, reject)
        for text in _every_string(alphabet, 6 if len(alphabet) == 5 else 5):
            found = list(
                markdown_links(text, image=image, label_min=label_min, target_min=target_min, reject_in_target=reject)
            )
            assert found == _spans(pattern, text), repr(text)

    @pytest.mark.parametrize(("image", "label_min", "target_min", "reject"), _STRICT_SHAPES)
    def test_random_text(self, image: bool | None, label_min: int, target_min: int | None, reject: str) -> None:
        pattern = _strict_reference(image, label_min, target_min, reject)
        for text in _random_strings(_STRICT_PIECES, 20_000, 16, seed=7):
            found = list(
                markdown_links(text, image=image, label_min=label_min, target_min=target_min, reject_in_target=reject)
            )
            assert found == _spans(pattern, text), repr(text)

    @pytest.mark.parametrize(
        ("text", "spans"),
        [
            ("[a](b)", [(0, 1, 2, 6)]),
            ("[[a](b)", [(0, 1, 3, 7)]),  # the label runs to the first "]", so it holds the second "["
            ("[a]()", []),
            ("[](b)", []),
            ("[a] (b)", []),
            ("[a](b(c)", [(0, 1, 2, 8)]),  # the target runs to the first ")"
            ("[a](b", []),
            ("[a](b) and [c](d)", [(0, 1, 2, 6), (11, 12, 13, 17)]),
            ("![a](b)", [(1, 2, 3, 7)]),  # a "!" stays outside the link
            ("[a][b](c)", [(3, 4, 5, 9)]),
        ],
    )
    def test_examples(self, text: str, spans: list[tuple[int, int, int, int]]) -> None:
        assert list(markdown_links(text)) == [LinkSpan(*span) for span in spans]

    def test_images_only(self) -> None:
        assert list(markdown_links("![a](b) [c](d)", image=True, label_min=0, target_min=0)) == [LinkSpan(0, 2, 3, 7)]

    def test_a_bang_before_the_bracket_belongs_to_the_link_when_either_is_asked_for(self) -> None:
        links = list(markdown_links("x![a](b) y[c](d) ![e](f)", image=None, label_min=0))

        assert [(link.start, link.end) for link in links] == [(1, 8), (10, 16), (17, 24)]

    def test_a_target_that_runs_into_a_rejected_character_is_not_a_link(self) -> None:
        text = "[a](b\x00c) [d](e)"

        assert list(markdown_links(text, reject_in_target="\x00")) == [LinkSpan(9, 10, 11, 15)]
        assert list(markdown_links(text)) == [LinkSpan(0, 1, 2, 8), LinkSpan(9, 10, 11, 15)]

    def test_a_refused_link_does_not_hide_the_link_that_starts_inside_it(self) -> None:
        # The first "[" fails (its target meets the rejected character before the first ")"), and the second "[" opens
        # a link whose label runs on past that ")".
        text = "[a](x [b\x00) c](d)"

        assert list(markdown_links(text, reject_in_target="\x00")) == [LinkSpan(6, 7, 12, 16)]


class TestNestedScanFindsWhatThePatternFinds:
    def test_every_short_text(self) -> None:
        for text in _every_string(("[", "]", "(", ")", "a", "!", "\n"), 6):
            assert list(nested_markdown_links(text)) == _spans(_NESTED_REFERENCE, text), repr(text)

    def test_random_text(self) -> None:
        for text in _random_strings(_NESTED_PIECES, 20_000, 16, seed=11):
            assert list(nested_markdown_links(text)) == _spans(_NESTED_REFERENCE, text), repr(text)

    @pytest.mark.parametrize(
        ("text", "spans"),
        [
            ("[a](b)", [(0, 1, 2, 6)]),
            ("[a [b] c](d)", [(0, 1, 8, 12)]),  # the label holds a bracketed word
            ("[a]() [c](d)", [(0, 1, 8, 12)]),  # an empty target closes nothing, so the label runs to the next "]("
            ("[a]())", []),
            ("![a](b) [c](d)", [(8, 9, 10, 14)]),  # an image is no link
            ("[a\nb](c)", [(0, 1, 4, 8)]),  # the label may span lines
            ("[a](b", []),
        ],
    )
    def test_examples(self, text: str, spans: list[tuple[int, int, int, int]]) -> None:
        assert list(nested_markdown_links(text)) == [LinkSpan(*span) for span in spans]


def _lazy_patterns(lazy_target: bool) -> Iterator[re.Pattern[str]]:
    """The lazy-label patterns the masked text is matched with: with and without a lookbehind, ``.`` with line breaks or not."""
    target = r".+?" if lazy_target else r"[^)]+"
    for lookbehind in ("", r"(?<!\!)"):
        for flags in (re.DOTALL, re.NOFLAG):
            yield re.compile(rf"{lookbehind}\[(.+?)\]\(({target})\)", flags)


class TestMaskedOpeners:
    @pytest.mark.parametrize("lazy_target", [False, True])
    def test_the_matches_do_not_change(self, lazy_target: bool) -> None:
        patterns = list(_lazy_patterns(lazy_target))
        for text in itertools.chain(
            _every_string(("[", "]", "(", ")", "a", "!", "\n"), 6),
            _random_strings(_NESTED_PIECES, 20_000, 16, seed=19),
        ):
            masked = mask_unclosable_openers(text, lazy_target=lazy_target)
            assert len(masked) == len(text)
            for pattern in patterns:
                original_matches = [(m.span(), m.span(1), m.span(2)) for m in pattern.finditer(text)]
                assert [(m.span(), m.span(1), m.span(2)) for m in pattern.finditer(masked)] == original_matches, repr(
                    text
                )

    @pytest.mark.parametrize(
        ("text", "lazy_target", "masked"),
        [
            ("[a](b)", False, "[a](b)"),
            ("[a](b) [c]", False, "[a](b) \x00c]"),  # no ``](`` after the second opener
            ("[[[", False, "\x00\x00\x00"),
            ("[a](", False, "\x00a]("),  # no ")" to close a target
            ("[a]())", False, "\x00a]())"),  # an empty target closes nothing
            ("[a]())", True, "[a]())"),  # a target may begin with the ")" that ends the first one
            ("[a]()", True, "\x00a]()"),
            ("[a](b)[c](d)[e", False, "[a](b)[c](d)\x00e"),
            ("](a)[", False, "](a)\x00"),
            ("", False, ""),
        ],
    )
    def test_examples(self, text: str, lazy_target: bool, masked: str) -> None:
        assert mask_unclosable_openers(text, lazy_target=lazy_target) == masked

    def test_a_text_without_unclosable_openers_is_returned_unchanged(self) -> None:
        text = "x [a](b) y"

        assert mask_unclosable_openers(text) is text


class TestReplacement:
    @staticmethod
    def _anchor(label: str, target: str, original: str) -> str:
        return f"<a href='{target}' title='{original}'>{label}</a>"

    def test_the_pieces_between_links_stay_and_the_rendering_goes_in_as_written(self) -> None:
        text = "see [a](b) and [c](d\\1) end"

        assert (
            replace_markdown_links(text, lambda label, target, _original: f"{label}={target}")
            == "see a=b and c=d\\1 end"
        )

    def test_a_text_without_links_is_returned_unchanged(self) -> None:
        text = "no links [here] or (there)"

        assert replace_markdown_links(text, self._anchor) is text
        assert replace_nested_markdown_links(text, self._anchor) is text

    @pytest.mark.parametrize(
        ("image", "label_min", "target_min", "reject"),
        [s.values[:4] for s in _STRICT_SHAPES if s.values[2] is not None],
    )
    def test_the_same_text_as_re_sub(self, image: bool | None, label_min: int, target_min: int, reject: str) -> None:
        pattern = _strict_reference(image, label_min, target_min, reject)
        for text in _random_strings(_STRICT_PIECES, 5_000, 16, seed=13):
            expected = pattern.sub(lambda m: self._anchor(m.group(1), m.group(2), m.group(0)), text)
            assert (
                replace_markdown_links(
                    text, self._anchor, image=image, label_min=label_min, target_min=target_min, reject_in_target=reject
                )
                == expected
            ), repr(text)

    def test_the_same_text_as_re_sub_for_nested_labels(self) -> None:
        for text in _random_strings(_NESTED_PIECES, 5_000, 16, seed=17):
            expected = _NESTED_REFERENCE.sub(lambda m: self._anchor(m.group(1), m.group(2), m.group(0)), text)
            assert replace_nested_markdown_links(text, self._anchor) == expected, repr(text)


_HOSTILE: dict[str, Callable[[int], str]] = {
    "bracket-open": lambda n: "[" * n,
    "bracket-then-close": lambda n: "[" * n + "]",
    "bracket-then-close-paren": lambda n: "[" * n + "](" + "a" * 3,
    "bracket-then-empty-target": lambda n: "[" * n + "]()",
    "image-open": lambda n: "![" * (n // 2),
    "link-without-close": lambda n: "[a](" * (n // 4),
    "link-with-empty-target": lambda n: "[a]()" * (n // 5),
    "link-closing-at-the-end": lambda n: "[a](" * (n // 4) + ")",
    "closer-only": lambda n: "](" * (n // 2),
    "link-run-into-rejected": lambda n: "[a](" * (n // 4) + "\x00)",
    "lines-of-openers": lambda n: "[\n" * (n // 2),
}


def _scan_in(text: str) -> float:
    """The least CPU seconds of two passes of every scan over ``text``; the least leaves out scheduler noise."""
    lazy_patterns = [list(_lazy_patterns(lazy)) for lazy in (False, True)]
    best = float("inf")
    for _ in range(2):
        started = time.process_time()
        for image, label_min, target_min in ((False, 1, 1), (True, 0, 1), (None, 0, 0), (True, 0, None)):
            for _span in markdown_links(
                text, image=image, label_min=label_min, target_min=target_min, reject_in_target="\x00"
            ):
                pass
        for _span in nested_markdown_links(text):
            pass
        for lazy_target, patterns in enumerate(lazy_patterns):
            masked = mask_unclosable_openers(text, lazy_target=bool(lazy_target))
            for pattern in patterns:
                for _match in pattern.finditer(masked):
                    pass
        best = min(best, time.process_time() - started)
    return best


class TestLinearTime:
    # The patterns took 0.9 s on 20,000 characters of the openers and, the lazy ones, 4.5 s on 4,000 characters of
    # ``[a](``. A scan that restarts from each opener but looks for its closer with ``str.find`` is quick enough at that
    # size to pass a fixed limit, so the text grows eightfold instead: linear time grows about eightfold, quadratic
    # time about sixty-four-fold.
    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_text(self, family: str) -> None:
        short = _scan_in(_HOSTILE[family](50_000))
        assert short < 1.0  # a scan that restarts from every opener fails here, before the long text is tried

        long = _scan_in(_HOSTILE[family](400_000))

        assert long < 24 * max(short, 0.001)
