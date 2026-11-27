"""The Obsidian import reads the image embeds its pattern read, in time proportional to the note.

The old pattern is the reference here, not the code under test. A name holds no ``]``, and the pattern read up to the
first ``]`` from every ``![[`` that did not close there: a vault note of 100,000 characters of ``![[a.txt`` took four
seconds.
"""

from __future__ import annotations

import hashlib
import itertools
import random
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from app.services.wiki.obsidian.adapter import _image_embeds, rewrite_image_embeds

_OLD_RE = re.compile(r"!\[\[([^\]]+\.(?:png|jpg|jpeg|gif|svg|webp|bmp|avif))\]\]", re.IGNORECASE)


def _old_embeds(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(1)) for m in _OLD_RE.finditer(text)]


_PIECES = (
    "![[", "[", "]", "]]", "a", ".png", ".SVG", " ", "\n", "![[a.png]]", "![[b c.jpeg]]", ".ſvg", ".txt", "!",
)  # fmt: skip


def _random_strings(count: int, longest: int, seed: int) -> Iterator[str]:
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(_PIECES) for _ in range(rng.randint(0, longest)))


class TestFindsWhatThePatternFound:
    @pytest.mark.parametrize("length", range(7))
    def test_every_text_of_that_length(self, length: int) -> None:
        for pieces in itertools.product(("![[", "[", "]", "]]", "a", ".png", ".SVG", " "), repeat=length):
            text = "".join(pieces)
            assert list(_image_embeds(text)) == _old_embeds(text), text

    def test_random_text(self) -> None:
        for text in _random_strings(40_000, 14, seed=61):
            assert list(_image_embeds(text)) == _old_embeds(text), repr(text)

    @pytest.mark.parametrize(
        ("text", "embeds"),
        [
            ("![[cat.png]]", [(0, 12, "cat.png")]),
            ("a ![[cat.PNG]] b ![[dog.jpeg]]", [(2, 14, "cat.PNG"), (17, 30, "dog.jpeg")]),
            ("![[a.ſvg]]", [(0, 10, "a.ſvg")]),  # the case rule is the pattern's own: "ſ" folds to "s"
            ("![[cat.txt]]", []),  # only images
            ("![[.png]]", []),  # a name is not empty
            ("![[cat.png]x]]", []),  # an embed closes at the first "]" or not at all
            ("![[cat.png]", []),
            ("![[a.txt ![[b.png]]", [(0, 19, "a.txt ![[b.png")]),  # the first opener takes the whole name
        ],
    )
    def test_examples(self, text: str, embeds: list[tuple[int, int, str]]) -> None:
        assert list(_image_embeds(text)) == embeds == _old_embeds(text)


class TestRewriteImageEmbeds:
    def test_copies_the_images_a_note_embeds_and_leaves_the_others(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        (vault / "img").mkdir(parents=True)
        (vault / "img" / "cat.png").write_bytes(b"png")
        assets = tmp_path / "assets"
        digest = hashlib.sha256(b"png").hexdigest()[:12]

        rewritten, copied = rewrite_image_embeds(
            "a ![[cat.png]] b ![[missing.png]] c ![[ cat.png]] d", vault / "note.md", vault, assets
        )

        assert rewritten == f"a ![cat]({digest}_cat.png) b ![[missing.png]] c ![cat]({digest}_cat.png) d"
        assert copied == 2
        assert (assets / f"{digest}_cat.png").read_bytes() == b"png"

    def test_an_embed_after_a_flood_of_openers_is_found(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "cat.png").write_bytes(b"png")
        digest = hashlib.sha256(b"png").hexdigest()[:12]

        rewritten, copied = rewrite_image_embeds(
            "![[a.txt" * 2_000 + "]x ![[cat.png]]", vault / "note.md", vault, tmp_path / "assets"
        )

        assert rewritten == "![[a.txt" * 2_000 + f"]x ![cat]({digest}_cat.png)"
        assert copied == 1


_HOSTILE: dict[str, Callable[[int], str]] = {
    "openers": lambda n: "![[" * (n // 3),
    "names-without-close": lambda n: "![[a.png" * (n // 8),
    "names-then-a-single-bracket": lambda n: "![[a.txt" * (n // 8) + "]x",
    "names-then-a-bracket-pair": lambda n: "![[a.txt" * (n // 8) + "]]",
    "single-brackets": lambda n: "![[a]x" * (n // 6),
    "embeds": lambda n: "![[a.png]] " * (n // 11),
}


def _scan_in(text: str) -> float:
    """The least CPU seconds of two scans of ``text``."""
    best = float("inf")
    for _ in range(2):
        started = time.process_time()
        list(_image_embeds(text))
        best = min(best, time.process_time() - started)
    return best


class TestLinearTime:
    # The note grows eightfold: linear time grows about eightfold, quadratic about sixty-four-fold.
    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_note(self, family: str) -> None:
        short = _scan_in(_HOSTILE[family](50_000))
        assert short < 1.0

        long = _scan_in(_HOSTILE[family](400_000))

        assert long < 24 * max(short, 0.001)
