"""Unicode normalization whose cost grows linearly with the text, however many combining marks it stacks.

[INPUT]
- (none)

[OUTPUT]
- NormalizationForm: The four forms ``unicodedata.normalize`` accepts
- normalize: ``unicodedata.normalize`` of the text in Stream-Safe Text Format
- stream_safe: The text with a joiner inserted where a run of combining marks would pass 30 non-starters

[POS]
The one entry to Unicode normalization for text that can come from a remote page, a file, a model or a user.
``unicodedata.normalize`` of CPython 3.11.15 and 3.13.13 sorts every run of combining marks by combining class with a
quadratic sort: 20,000 marks in alternating classes cost about 0.1 s and 40,000 about 0.4 s of CPU with the interpreter
lock held (3.13.15 sorts the 20,000 in 0.5 ms). UAX #15 section 13 answers that with the Stream-Safe Text Format, at
most 30 consecutive non-starters in the NFKD, and ``stream_safe`` produces it, so the cost is linear on every
interpreter. Natural text never comes near that limit, so it passes through unchanged; only text built to exceed it
differs.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from collections.abc import Iterable
from functools import cache
from typing import Final, Literal, NamedTuple

NormalizationForm = Literal["NFC", "NFD", "NFKC", "NFKD"]

# UAX #15 section 13: the longest run of non-starters a Stream-Safe text may hold in its NFKD.
_MAX_NON_STARTERS: Final = 30
# COMBINING GRAPHEME JOINER: combining class 0, so neither the reordering nor the composition reaches across it.
_GRAPHEME_JOINER: Final = "\u034f"


class _Profile(NamedTuple):
    """The combining marks one character adds to a text, counted over its NFKD."""

    leading: int  # marks at the start of the NFKD: they extend the run before the character
    trailing: int  # marks at the end of the NFKD: they open the run after the character
    carries: bool  # the NFKD is marks only: the run before the character goes on through it


class _Table(NamedTuple):
    profiles: dict[str, _Profile]  # every character whose NFKD holds a combining mark
    long_runs: re.Pattern[str]  # runs of characters that may hold marks, long enough to pass the limit


_STARTER = _Profile(0, 0, False)


def _profile(char: str) -> _Profile | None:
    marks = [unicodedata.combining(part) != 0 for part in unicodedata.normalize("NFKD", char)]
    if not any(marks):
        return None
    leading = next((index for index, mark in enumerate(marks) if not mark), len(marks))
    trailing = next((index for index, mark in enumerate(reversed(marks)) if not mark), len(marks))
    return _Profile(leading, trailing, all(marks))


def _character_class(chars: Iterable[str]) -> str:
    """The body of a regex character class that matches exactly ``chars``, consecutive code points as ranges."""
    ranges: list[list[int]] = []
    for code in sorted(map(ord, chars)):
        if ranges and code == ranges[-1][1] + 1:
            ranges[-1][1] = code
        else:
            ranges.append([code, code])
    return "".join(f"\\U{low:08x}" if low == high else f"\\U{low:08x}-\\U{high:08x}" for low, high in ranges)


@cache
def _table() -> _Table:
    """The characters that hold a combining mark once decomposed, read from the interpreter's own Unicode database.

    Built on the first non-ASCII text, a scan of every code point that takes about 0.1 s, and kept for the process.
    The database is the one ``unicodedata.normalize`` itself uses, so the guard stays exact across Unicode versions.
    """
    profiles: dict[str, _Profile] = {}
    for code in range(sys.maxunicode + 1):
        char = chr(code)
        # A character holds a mark when it is one or decomposes into one; a Hangul syllable decomposes into starters.
        if (unicodedata.combining(char) or unicodedata.decomposition(char)) and (profile := _profile(char)):
            profiles[char] = profile
    widest = max(max(profile.leading, profile.trailing) for profile in profiles.values())
    # Passing the limit takes a run at least this long, whatever characters make it up.
    shortest = -(-(_MAX_NON_STARTERS + 1) // widest)
    # The regex engine tests a character beyond the BMP against every range of a class one by one, 20 times slower than
    # a BMP character against the class bitmap, so those characters are one coarse range: a run that is long enough
    # without holding marks is walked and found harmless.
    astral_end = max(map(ord, profiles))
    bmp = (char for char in profiles if ord(char) <= 0xFFFF)
    return _Table(profiles, re.compile(f"[{_character_class(bmp)}\\U00010000-\\U{astral_end:08x}]{{{shortest},}}"))


def _guard(run: str) -> str:
    """``run`` with a joiner before each character that would pass the limit.

    ``run`` is a maximal run of characters that may hold marks, so the character before it holds none and the count of
    non-starters starts at zero.
    """
    profiles = _table().profiles
    pieces: list[str] = []
    marks = 0
    for char in run:
        profile = profiles.get(char, _STARTER)
        if marks + profile.leading > _MAX_NON_STARTERS:
            pieces.append(_GRAPHEME_JOINER)
            marks = 0
        marks = marks + profile.trailing if profile.carries else profile.trailing
        pieces.append(char)
    return "".join(pieces)


def stream_safe(text: str) -> str:
    """``text`` in Stream-Safe Text Format: never more than 30 consecutive non-starters in its NFKD.

    UAX #15 counts the non-starters per character, so the count follows the characters in order and a joiner goes in
    front of the one that would pass 30. Text that is within the limit comes back unchanged, and the cost is linear:
    one scan finds the runs that are long enough to pass the limit, and only those runs are walked character by
    character.
    """
    if text.isascii():
        return text
    return _table().long_runs.sub(lambda match: _guard(match.group()), text)


def normalize(form: NormalizationForm, text: str) -> str:
    """``unicodedata.normalize(form, text)`` in time linear in ``len(text)``.

    The result is the same for every text that is within the limit of ``stream_safe``. A text past it is normalized
    in segments of at most 30 marks that the joiner keeps apart, so marks far apart in the original are not reordered
    or composed across the cut.
    """
    return unicodedata.normalize(form, stream_safe(text))
