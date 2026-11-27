"""The wiki's Markdown link scans read what the patterns they replaced read, in time proportional to the page.

The old patterns are the reference. A page the user clips or imports can hold any run of ``[`` and ``![``; the old
patterns read the rest of the page from each opener that never closes (20,000 openers: about 0.9 s), so a page of
100,000 held the process for half a minute.
"""

from __future__ import annotations

import itertools
import random
import re
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import ClassVar

import pytest

from myrm_agent_harness.toolkits.wiki.core.config import WikiConfig
from myrm_agent_harness.toolkits.wiki.core.structure import WikiStructure
from myrm_agent_harness.toolkits.wiki.pipeline.ingress.asset_store import (
    _asset_relpath_from_raw,
    localize_public_markdown_images,
    rewrite_markdown_asset_refs,
)
from myrm_agent_harness.toolkits.wiki.pipeline.ingress.publish import _has_markdown_image
from myrm_agent_harness.toolkits.wiki.retrieval.asset_index import WikiAssetIndexer
from myrm_agent_harness.toolkits.wiki.retrieval.graph_store import _mention_patterns
from myrm_agent_harness.toolkits.wiki.retrieval.heading_text import _strip_wikilinks, clean_heading_text

_PIECES = (
    "![", "[", "]", "(", ")", "!", "a", " ", "](", "![alt](https://a.test/x.png)", "![](b.png)", "![x](b.png \"t\")",
    "[l](u)", "[[", "]]", "|", "`c`", "**b**", "é", "https://a.test/x.png", "b.png", "#",
)  # fmt: skip


def _texts(count: int, longest: int, seed: int, *, newlines: bool = False) -> list[str]:
    rng = random.Random(seed)
    pieces = (*_PIECES, "\n") if newlines else _PIECES
    return ["".join(rng.choice(pieces) for _ in range(rng.randint(0, longest))) for _ in range(count)]


def _cpu_seconds(work: Callable[[], object]) -> float:
    """The least CPU time of two runs; the least leaves out scheduler noise."""
    best = float("inf")
    for _ in range(2):
        started = time.process_time()
        work()
        best = min(best, time.process_time() - started)
    return best


def _assert_linear(make: Callable[[int], str], work: Callable[[str], object], small: int = 50_000) -> None:
    """The page grows eightfold: linear time grows about eightfold, quadratic about sixty-four-fold."""
    short = _cpu_seconds(lambda: work(make(small)))
    assert short < 1.0

    assert _cpu_seconds(lambda: work(make(small * 8))) < 24 * max(short, 0.001)


_HOSTILE: dict[str, Callable[[int], str]] = {
    "image-openers": lambda n: "![" * (n // 2),
    "openers": lambda n: "[" * n,
    "wikilink-openers": lambda n: "[[" * (n // 2),
    "links-without-close": lambda n: "[a](" * (n // 4),
    "links-closing-at-the-end": lambda n: "[a](" * (n // 4) + ")",
    "image-after-links": lambda n: "![d](p.png)\n" + "![" * (n // 2),
}


class TestAssetReferenceRewrite:
    _URLS: ClassVar[dict[str, str]] = {"https://a.test/x.png": "f1.png", "b.png": "f2.png"}

    @staticmethod
    def _old(markdown: str, url_to_filename: dict[str, str], raw_relative: str) -> str:
        image = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

        def replace(match: re.Match[str]) -> str:
            full = match.group(0)
            alt_match = re.match(r"!\[(.*?)\]", full)
            alt = alt_match.group(1) if alt_match else ""
            filename = url_to_filename.get(match.group(1).strip().split(" ", 1)[0])
            if not filename:
                return full
            return f"![{alt}]({_asset_relpath_from_raw(raw_relative, filename)})"

        return image.sub(replace, markdown)

    def test_rewrites_what_the_pattern_rewrote(self) -> None:
        for text in _texts(20_000, 14, seed=31):
            assert rewrite_markdown_asset_refs(text, self._URLS, raw_relative="clips/a.md") == self._old(
                text, self._URLS, "clips/a.md"
            ), repr(text)

    def test_an_image_is_pointed_at_the_stored_asset_and_loses_its_title(self) -> None:
        text = 'see ![chart](b.png "a title") and ![gone](https://other.test/y.png)'

        assert rewrite_markdown_asset_refs(text, self._URLS, raw_relative="clips/a.md") == (
            "see ![chart](../../wiki/assets/f2.png) and ![gone](https://other.test/y.png)"
        )

    def test_an_alt_text_over_several_lines_is_kept(self) -> None:
        # The old code read the alt text with a pattern whose ``.`` stops at a line break and dropped it.
        text = "![first line\nsecond line](b.png)"

        assert rewrite_markdown_asset_refs(text, self._URLS, raw_relative="clips/a.md") == (
            "![first line\nsecond line](../../wiki/assets/f2.png)"
        )

    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_page(self, family: str) -> None:
        _assert_linear(
            _HOSTILE[family], lambda text: rewrite_markdown_asset_refs(text, self._URLS, raw_relative="clips/a.md")
        )


class TestImageReferenceScan:
    def test_the_images_a_page_embeds_are_found_after_a_flood_of_unclosed_openers(
        self, wiki_structure: WikiStructure
    ) -> None:
        page = wiki_structure.concepts_dir / "Payment.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("# Payment\n\n![diagram](payment-flow.png)\n![](bare.png)\n" + "![" * 100_000, encoding="utf-8")
        indexer = WikiAssetIndexer(wiki_structure, WikiConfig(enable_asset_index=True))

        started = time.process_time()
        refs = indexer.scan_image_references()

        assert refs == {"bare.png": ["Payment"], "payment-flow.png": ["Payment"]}
        assert time.process_time() - started < 2.0


class TestImageDownload:
    async def test_only_the_images_a_page_embeds_are_fetched(
        self, wiki_structure: WikiStructure, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fetched: list[str] = []

        async def refuse(url: str, **_options: object) -> SimpleNamespace:
            fetched.append(url)
            return SimpleNamespace(status_code=404, headers={}, content=b"")

        monkeypatch.setattr("myrm_agent_harness.toolkits.wiki.pipeline.ingress.asset_store.secure_get", refuse)
        page = (
            "[a page](https://a.test/page) ![](https://a.test/bare.png) "
            '![alt](https://a.test/titled.png "a title") ![inline](data:image/png;base64,AAAA) '
            "![again](https://a.test/bare.png)"
        )

        rewritten, stats = await localize_public_markdown_images(wiki_structure, page, base_url="")

        assert fetched == ["https://a.test/bare.png", "https://a.test/titled.png"]
        assert (rewritten, stats.failed) == (page, 2)


class TestImagePresence:
    def test_finds_what_the_pattern_found(self) -> None:
        pattern = re.compile(r"!\[[^\]]*\]\(")
        for text in _texts(30_000, 14, seed=33, newlines=True):
            assert _has_markdown_image(text) == bool(pattern.search(text)), repr(text)

    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_page(self, family: str) -> None:
        _assert_linear(_HOSTILE[family], _has_markdown_image)


def _old_clean_heading_text(raw_heading: str) -> str:
    text = raw_heading.strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"~~([^~]+)~~", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


class TestWikilinkStrip:
    _OLD = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")

    def test_strips_what_the_pattern_stripped_on_every_short_text(self) -> None:
        for length in range(8):
            for letters in itertools.product(("[", "]", "|", "a", "\n"), repeat=length):
                text = "".join(letters)
                assert _strip_wikilinks(text) == self._OLD.sub(r"\1", text), repr(text)

    def test_strips_what_the_pattern_stripped_on_random_text(self) -> None:
        for text in _texts(30_000, 14, seed=39, newlines=True):
            assert _strip_wikilinks(text) == self._OLD.sub(r"\1", text), repr(text)

    @pytest.mark.parametrize(
        ("text", "stripped"),
        [
            ("[[Target]] and [[Page|Shown]]", "Target and Shown"),
            ("[[a|]]", "a|"),  # an empty display falls back to the whole body
            ("[[]]", "[[]]"),
            ("[[a]", "[[a]"),
            ("[[a [[b]]", "a [[b"),  # the body runs to the first "]", so it holds the second opener
        ],
    )
    def test_examples(self, text: str, stripped: str) -> None:
        assert _strip_wikilinks(text) == stripped

    def test_a_text_without_wikilinks_is_returned_unchanged(self) -> None:
        text = "no [wikilinks] here"

        assert _strip_wikilinks(text) is text

    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_text(self, family: str) -> None:
        _assert_linear(_HOSTILE[family], _strip_wikilinks)


class TestHeadingText:
    def test_cleans_what_the_patterns_cleaned(self) -> None:
        for text in _texts(30_000, 14, seed=35, newlines=True):
            heading = f"## {text}"
            assert clean_heading_text(heading) == _old_clean_heading_text(heading), repr(heading)

    def test_examples(self) -> None:
        assert (
            clean_heading_text("## [[Target|Shown]] and ![alt](u.png) and [text](https://x.dev)")
            == "Shown and alt and text"
        )
        assert clean_heading_text("### **Bold** `code` ~~gone~~") == "Bold code gone"

    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_heading(self, family: str) -> None:
        _assert_linear(_HOSTILE[family], lambda text: clean_heading_text(f"# {text}"))


class TestMentionPatterns:
    def test_the_link_pattern_finds_a_link_that_names_the_page(self) -> None:
        link_pattern = _mention_patterns(("Payment Flow",))[0]

        assert link_pattern.search("see [the flow](https://x.dev/payment flow/doc) here")
        assert link_pattern.search("see [the flow](https://x.dev/other) here") is None

    def test_finds_what_the_dynamic_patterns_found(self) -> None:
        for name in ("py", "a.b", "华创", "Payment"):
            old = re.compile(rf"\[[^\]]+\]\([^)]*{re.escape(name)}[^)]*\)", re.IGNORECASE)
            new = _mention_patterns((name,))[0]
            for text in _texts(10_000, 14, seed=37):
                line = f"{text} [x]({text}{name}{text}) {text}"
                assert [m.span() for m in new.finditer(line)] == [m.span() for m in old.finditer(line)], repr(line)

    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    def test_time_grows_in_proportion_to_the_page(self, family: str) -> None:
        pattern = _mention_patterns(("payment",))[0]
        _assert_linear(_HOSTILE[family], lambda text: pattern.search(text), small=10_000)
