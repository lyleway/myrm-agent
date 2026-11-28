"""Tests for the L1 static long-page fallback gate."""

from __future__ import annotations

import random
import re
import time
import unicodedata
from collections.abc import Callable

import pytest

from myrm_agent_harness.toolkits.web_fetch.fetchers.protocols import FetcherType, FetchResult
from myrm_agent_harness.toolkits.web_fetch.processing.pipeline import ContentPipeline
from myrm_agent_harness.toolkits.web_fetch.processing.static_page_gate import (
    count_visible_markdown_chars,
    should_fallback_to_raw_markdown,
)


def _long_raw_markdown(chars: int = 3500) -> str:
    unit = "ManualEntry "
    return unit * ((chars // len(unit)) + 5)


class TestStaticPageGateConditions:
    """单测覆盖每一个门不触发的条件。"""

    def test_accepts_l1_2xx_long_dense_markdown(self) -> None:
        raw = _long_raw_markdown(3600)
        assert (
            should_fallback_to_raw_markdown(
                fetcher_type=FetcherType.HTTP,
                status_code=200,
                raw_markdown=raw,
                html_length=40_000,
            )
            is True
        )

    @pytest.mark.parametrize("status_code", [199, 301, 403, 404, 500, 503])
    def test_rejects_non_2xx_status_codes(self, status_code: int) -> None:
        raw = _long_raw_markdown(3600)
        assert (
            should_fallback_to_raw_markdown(
                fetcher_type=FetcherType.HTTP,
                status_code=status_code,
                raw_markdown=raw,
                html_length=10_000,
            )
            is False
        )

    def test_rejects_below_3000_visible_chars(self) -> None:
        raw = "a" * 2999
        assert (
            should_fallback_to_raw_markdown(
                fetcher_type=FetcherType.HTTP,
                status_code=200,
                raw_markdown=raw,
                html_length=5_000,
            )
            is False
        )

    def test_rejects_below_4_percent_ratio(self) -> None:
        raw = "a" * 3500
        # 3500 / 100_000 = 3.5% < 4%
        assert (
            should_fallback_to_raw_markdown(
                fetcher_type=FetcherType.HTTP,
                status_code=200,
                raw_markdown=raw,
                html_length=100_000,
            )
            is False
        )

    def test_rejects_empty_html_or_markdown(self) -> None:
        assert (
            should_fallback_to_raw_markdown(
                fetcher_type=FetcherType.HTTP,
                status_code=200,
                raw_markdown="",
                html_length=10_000,
            )
            is False
        )
        assert (
            should_fallback_to_raw_markdown(
                fetcher_type=FetcherType.HTTP,
                status_code=200,
                raw_markdown="a" * 4000,
                html_length=0,
            )
            is False
        )

    def test_images_link_urls_and_punctuation_do_not_inflate_visible_count(self) -> None:
        noise = (
            "![alt text here](https://cdn.example.com/" + "x" * 200 + ".png) "
            "[go](https://example.com/" + "y" * 200 + ") "
            "--- *** ||| !!! "
        ) * 50
        assert count_visible_markdown_chars(noise) == len("go") * 50
        assert (
            should_fallback_to_raw_markdown(
                fetcher_type=FetcherType.HTTP,
                status_code=200,
                raw_markdown=noise,
                html_length=50_000,
            )
            is False
        )


_OLD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_OLD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _count_with_the_old_patterns(raw_markdown: str) -> int:
    text_only = _OLD_LINK_RE.sub(r"\1", _OLD_IMAGE_RE.sub(" ", raw_markdown))
    return sum(1 for ch in text_only if not ch.isspace() and unicodedata.category(ch)[0] not in ("P", "S", "C", "Z"))


class TestLinkSyntaxScan:
    """Unclosed ``[`` must cost time in proportion to the page, as the patterns did not (20,000 of them: about 0.9 s)."""

    def test_counts_what_the_patterns_counted(self) -> None:
        rng = random.Random(21)
        pieces = ("[", "]", "(", ")", "!", "a", "b", " ", "![", "](", "[x](y)", "![i](j)", "\n", "é", "—")
        for _ in range(20_000):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 16)))
            assert count_visible_markdown_chars(text) == _count_with_the_old_patterns(text), repr(text)

    @pytest.mark.parametrize(
        "make",
        [lambda n: "[" * n, lambda n: "![" * (n // 2), lambda n: "[a](" * (n // 4), lambda n: "[a](" * (n // 4) + ")"],
        ids=["openers", "image-openers", "links-without-close", "links-closing-at-the-end"],
    )
    def test_time_grows_in_proportion_to_the_page(self, make: Callable[[int], str]) -> None:
        def cpu_seconds(size: int) -> float:
            text = make(size)
            best = float("inf")
            for _ in range(2):
                started = time.process_time()
                count_visible_markdown_chars(text)
                best = min(best, time.process_time() - started)
            return best

        short = cpu_seconds(50_000)
        assert short < 1.0

        assert cpu_seconds(400_000) < 24 * max(short, 0.001)


def test_pipeline_delivers_link_heavy_static_index_page_on_l1_without_reconverting() -> None:
    """man7/Gutenberg 风格静态长目录页在 L1 被剪枝剪空后，由门控复用同一次转换的 raw_markdown 交付。"""
    links = "".join(f'<li><a href="/man/section2/syscall_{i}.html">syscall_entry_{i:04d}</a></li>' for i in range(260))
    html = f"<html><head><title>Man Pages Index</title></head><body><div><ul>{links}</ul></div></body></html>"
    fetch_result = FetchResult(
        html=html,
        url="https://man7.example.org/linux/man-pages/dir_section_2.html",
        status_code=200,
        fetcher_type=FetcherType.HTTP,
    )

    pipeline = ContentPipeline(use_raw_markdown=False)
    doc = pipeline.process(fetch_result)

    assert doc is not None
    assert "syscall_entry_0000" in doc.page_content
    assert "syscall_entry_0259" in doc.page_content
    assert doc.metadata["title"] == "Man Pages Index"


def test_pipeline_still_rejects_thin_js_shell_on_l1() -> None:
    """薄壳页（无正文）不被误放行，仍返回 None 以便升级或报失败。"""
    shell_html = (
        "<html><head><title>App</title></head><body>"
        "<div id='root'>Loading...</div>"
        + ("<script>window.__DATA__='" + "z" * 500 + "';</script>") * 20
        + "</body></html>"
    )
    fetch_result = FetchResult(
        html=shell_html,
        url="https://spa.example.org/app",
        status_code=200,
        fetcher_type=FetcherType.HTTP,
    )

    assert ContentPipeline(use_raw_markdown=False).process(fetch_result) is None


@pytest.mark.asyncio
async def test_engine_five_consecutive_static_index_fetches_deliver_on_l1() -> None:
    """静态长目录页连续 5 次访问都在 L1 交付，不记录失败。"""
    from unittest.mock import AsyncMock

    from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine

    links = "".join(f'<li><a href="/man/section2/syscall_{i}.html">syscall_entry_{i:04d}</a></li>' for i in range(260))
    html = (
        "<html><head><title>Man Pages Index</title></head>"
        f'<body><div class="index"><ul>{links}</ul></div></body></html>'
    )
    url = "https://man7-g9-gate.example.org/linux/man-pages/dir_section_2.html"

    engine = FetchEngine(allow_private_networks=True)
    engine._http_fetcher.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=FetchResult(html=html, url=url, status_code=200, fetcher_type=FetcherType.HTTP)
    )
    try:
        for _ in range(5):
            doc = await engine.crawl(url, force_refresh=True)
            assert doc is not None
            assert "syscall_entry_0000" in doc.page_content
    finally:
        await engine.shutdown()
