"""Whether an imported page still embeds a remote image is read in time proportional to the page.

The old pattern ``!\\[[^\\]]*\\]\\(\\s*<?https?://`` read the rest of the page from every ``![`` that never closed
(20,000 of them: about 0.9 s), so a page of 100,000 held the sync worker for half a minute.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable

import pytest

from app.services.wiki.source_sync.ima.content import _has_remote_image

_OLD_REMOTE_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?https?://", re.IGNORECASE)
_PIECES = (
    "![", "[", "]", "(", ")", "!", "a", " ", "\n", "](", "http://", "https://", "HTTPS://", "<", ">", "![i](https://x.y/a.png)",
    "![i](a.png)", "![i](<https://x.y>)", "![i]( \thttp://x.y)", "ftp://", "é",
)  # fmt: skip


def test_finds_what_the_pattern_found() -> None:
    rng = random.Random(51)
    for _ in range(30_000):
        text = "".join(rng.choice(_PIECES) for _ in range(rng.randint(0, 12)))
        assert _has_remote_image(text) == bool(_OLD_REMOTE_IMAGE_RE.search(text)), repr(text)


@pytest.mark.parametrize(
    ("markdown", "remote"),
    [
        ("![chart](https://cdn.example.com/a.png)", True),
        ("![chart](  <HTTP://cdn.example.com/a.png>)", True),
        ("![chart](../../wiki/assets/abc.png)", False),
        ("![chart](ftp://cdn.example.com/a.png)", False),
        ("[link](https://example.com)", False),  # a link is not an image
        ("![local](a.png) and ![remote](http://x.test/b.png)", True),
        ("![a![b](c.png) ![d](https://x.test/e.png)", True),
        ("![never closed", False),
        ("", False),
    ],
)
def test_examples(markdown: str, remote: bool) -> None:
    assert _has_remote_image(markdown) is remote


@pytest.mark.parametrize(
    "make",
    [
        lambda n: "![" * (n // 2),
        lambda n: "![](a.png) " * (n // 11),
        lambda n: "![a](" * (n // 5),
        lambda n: "![local](a.png)" * (n // 15) + "![" * 10,
    ],
    ids=["image-openers", "local-images", "images-without-close", "local-images-then-openers"],
)
def test_time_grows_in_proportion_to_the_page(make: Callable[[int], str]) -> None:
    def cpu_seconds(size: int) -> float:
        text = make(size)
        best = float("inf")
        for _ in range(2):
            started = time.process_time()
            _has_remote_image(text)
            best = min(best, time.process_time() - started)
        return best

    short = cpu_seconds(50_000)
    assert short < 1.0

    assert cpu_seconds(400_000) < 24 * max(short, 0.001)
