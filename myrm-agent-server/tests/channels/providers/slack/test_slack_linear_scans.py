"""The converter's scans for ``<...>`` find what the patterns they replace found, and its links and mentions come out as
the patterns gave them, in linear time.

The patterns below are the ones the scans replace. They are the reference, not the code under test: backtracking from
every opener makes a line of unclosed ``<`` or ``[`` cost the square of its length. The scans must return the same
spans on every short text over a small alphabet, on random longer ones, and take time in proportion to the length of a
hostile line.
"""

from __future__ import annotations

import itertools
import random
import re
import time
from collections.abc import Callable, Iterator

import pytest

from app.channels.providers.slack.format_converter import (
    _angle_spans,
    _convert_markdown_link,
    _convert_markdown_links,
    _escape_slack_content,
    _escape_slack_segment,
    _is_allowed_slack_token,
    md_to_slack_mrkdwn,
    neutralize_broadcast_mentions,
)

_REFERENCE_ANGLE = re.compile(r"<[^>\n]+>")
_REFERENCE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_REFERENCE_BROADCAST = re.compile(r"<!(?:here|channel|everyone|subteam\^[^>\n|]*)(?:\|[^>\n]*)?>", re.IGNORECASE)


def _every_string(alphabet: tuple[str, ...], longest: int) -> Iterator[str]:
    for length in range(longest + 1):
        for letters in itertools.product(alphabet, repeat=length):
            yield "".join(letters)


def _random_strings(pieces: tuple[str, ...], count: int, longest: int, seed: int) -> Iterator[str]:
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(pieces) for _ in range(rng.randint(0, longest)))


def _reference_escape(text: str) -> str:
    """``_escape_slack_content`` as it was written with the backtracking pattern."""
    if not text:
        return ""
    if "&" not in text and "<" not in text and ">" not in text:
        return text
    parts: list[str] = []
    last_end = 0
    for match in _REFERENCE_ANGLE.finditer(text):
        parts.append(_escape_slack_segment(text[last_end : match.start()]))
        token = match.group(0)
        parts.append(token if _is_allowed_slack_token(token) else _escape_slack_segment(token))
        last_end = match.end()
    parts.append(_escape_slack_segment(text[last_end:]))
    return "".join(parts)


def _reference_links(line: str) -> str:
    return _REFERENCE_LINK.sub(lambda match: _convert_markdown_link(match.group(1), match.group(2), match.group(0)), line)


def _reference_neutralize(text: str) -> str:
    return _REFERENCE_BROADCAST.sub(lambda match: _escape_slack_segment(match.group(0)), text)


_ANGLE_PIECES = ("<", ">", "\n", "a", "!", " ", "<@U1>", "<#C1|x>", "<https://a.b>", "<!date^1^{x}|y>", "<!here>", "&", "<>")
_LINK_PIECES = (
    "[",
    "]",
    "(",
    ")",
    "a",
    " ",
    "](",
    "[]",
    "()",
    "https://x.y",
    "@U1",
    "#C1",
    "!here",
    "mailto:a@b.c",
    "<",
    "**",
    "\n",
)
_BROADCAST_PIECES = ("<!", "<", "here", "HERE", "channel", "everyone", "subteam^", "S1", "|", ">", "\n", "x", " ")


class TestSameSpansAsTheBacktrackingPatterns:
    def test_angle_spans_on_every_short_text(self) -> None:
        for text in _every_string(("<", ">", "\n", "a"), 8):
            assert list(_angle_spans(text)) == [m.span() for m in _REFERENCE_ANGLE.finditer(text)], repr(text)

    def test_angle_spans_on_random_text(self) -> None:
        for text in _random_strings(_ANGLE_PIECES, 40_000, 14, seed=1):
            assert list(_angle_spans(text)) == [m.span() for m in _REFERENCE_ANGLE.finditer(text)], repr(text)

    @pytest.mark.parametrize(
        ("text", "spans"),
        [
            ("<a>", [(0, 3)]),
            ("x <a> y <b> z", [(2, 5), (8, 11)]),
            ("<a<b>", [(0, 5)]),  # a ``<`` inside a span belongs to it
            ("<>", []),
            ("<>a<b>", [(3, 6)]),
            ("<a\nb>", []),  # a span never crosses a line break
            ("<a\n<b>", [(3, 6)]),
            ("<a", []),
            ("a > b < c", []),
        ],
    )
    def test_angle_spans_examples(self, text: str, spans: list[tuple[int, int]]) -> None:
        assert list(_angle_spans(text)) == spans


class TestSameTextAsTheBacktrackingPatterns:
    def test_the_audience_notifying_tokens_on_every_short_text(self) -> None:
        for text in _every_string(_BROADCAST_PIECES[:8], 5):
            assert neutralize_broadcast_mentions(text) == _reference_neutralize(text), repr(text)

    def test_the_audience_notifying_tokens_on_random_text(self) -> None:
        for text in _random_strings(_BROADCAST_PIECES, 60_000, 12, seed=3):
            assert neutralize_broadcast_mentions(text) == _reference_neutralize(text), repr(text)

    def test_a_token_inside_an_unclosed_one_is_still_escaped(self) -> None:
        assert neutralize_broadcast_mentions("<!x <!here> y") == "<!x &lt;!here&gt; y"

    def test_escaping_on_random_text(self) -> None:
        for text in _random_strings(_ANGLE_PIECES, 40_000, 14, seed=4):
            assert _escape_slack_content(text) == _reference_escape(text), repr(text)

    def test_links_on_random_text(self) -> None:
        for text in _random_strings(_LINK_PIECES, 40_000, 14, seed=5):
            assert _convert_markdown_links(text) == _reference_links(text), repr(text)


_HOSTILE: dict[str, Callable[[int], str]] = {
    "angle-open": lambda n: "<" * n,
    "angle-word": lambda n: "<a" * (n // 2),
    "angle-lines": lambda n: "<\n" * (n // 2),
    "bracket-open": lambda n: "[" * n,
    "bracket-then-close": lambda n: "[" * n + "]",
    "bracket-pairs": lambda n: "[a]" * (n // 3),
    "link-without-close": lambda n: "[a](" * (n // 4),
    "subteam-open": lambda n: "<!subteam^" * (n // 10),
    "bang-open": lambda n: "<!" * (n // 2),
    "here-open": lambda n: "<!here" * (n // 6),
}


def _convert_in(text: str) -> float:
    """The least CPU seconds of two conversions of ``text``; the least leaves out scheduler and collector noise."""
    best = float("inf")
    for _ in range(2):
        started = time.process_time()
        md_to_slack_mrkdwn(text)
        neutralize_broadcast_mentions(text)
        best = min(best, time.process_time() - started)
    return best


class TestLinearTime:
    # The backtracking patterns took 1.3 s on 20,000 characters of these and over 5 s on 40,000, all of it on the event
    # loop that serves every channel. A scan that restarts from each opener but looks for its closer with ``str.find``
    # is quick enough at that size to pass a fixed limit, so the line grows eightfold instead: linear time grows about
    # eightfold, quadratic time about sixty-four-fold.
    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_line(self, family: str) -> None:
        short = _convert_in(_HOSTILE[family](50_000))
        assert short < 1.0  # a pattern that restarts from every opener fails here, before the long line is tried

        long = _convert_in(_HOSTILE[family](400_000))

        assert long < 24 * max(short, 0.001)

    def test_unclosed_openers_do_not_stop_the_lines_after_them(self) -> None:
        text = "<" * 30_000 + "\n[" * 3 + "\n<@U1> [a](https://x.y) <!here>"

        converted = md_to_slack_mrkdwn(text)

        assert converted == "&lt;" * 30_000 + "\n[\n[\n[\n<@U1> <https://x.y|a> &lt;!here&gt;"
