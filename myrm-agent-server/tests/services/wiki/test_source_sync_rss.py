"""Unit tests for RSS wiki source sync."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.wiki.source_sync import rss
from app.services.wiki.source_sync.rss import sync_rss_feeds_to_wiki
from app.services.wiki.source_sync.schemas import WikiSourceSyncResult
from tests.support.loopback_origin import LoopbackOrigin, page, redirect

_UNREACHABLE = "the address could not be reached"


def _publish_mock() -> AsyncMock:
    return AsyncMock(return_value=MagicMock(written=True, skipped=False, conflict_skipped=False, security_blocked=False))


async def _sync_feed(body: str) -> tuple[WikiSourceSyncResult, AsyncMock]:
    """Sync one feed whose server answers with ``body``; returns the run result and the mocked publish call."""
    publish = _publish_mock()
    response = MagicMock()
    response.text = body
    with (
        patch("app.services.wiki.source_sync.rss.fetch_configured_url", AsyncMock(return_value=response)),
        patch("app.services.wiki.source_sync.rss.publish_source_markdown", publish),
    ):
        result = await sync_rss_feeds_to_wiki(
            MagicMock(),
            feed_urls=["https://example.com/feed.xml"],
            max_items=5,
            auto_compile=False,
            compiler_enqueue=None,
        )
    return result, publish


async def _sync_urls(feed_urls: list[str]) -> tuple[WikiSourceSyncResult, AsyncMock]:
    """Sync the feeds at ``feed_urls`` over the network; only the publication is mocked."""
    publish = _publish_mock()
    with patch("app.services.wiki.source_sync.rss.publish_source_markdown", publish):
        result = await sync_rss_feeds_to_wiki(
            MagicMock(), feed_urls=feed_urls, max_items=5, auto_compile=False, compiler_enqueue=None
        )
    return result, publish


def _rss_item(description: str) -> str:
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><item><title>T</title><guid>g</guid>'
        f"<description><![CDATA[{description}]]></description></item></channel></rss>"
    )


@pytest.mark.asyncio
async def test_sync_rss_skips_invalid_url() -> None:
    structure = MagicMock()
    result = await sync_rss_feeds_to_wiki(
        structure,
        feed_urls=["ftp://bad.example/feed"],
        max_items=5,
        auto_compile=False,
        compiler_enqueue=None,
    )
    assert result.failed == 1
    assert result.published == 0


@pytest.mark.asyncio
async def test_sync_rss_publishes_atom_entry() -> None:
    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Hello</title>
        <id>entry-1</id>
        <summary>Body text</summary>
        <link href="https://example.com/a"/>
      </entry>
    </feed>
    """
    result, publish = await _sync_feed(atom)

    assert result.published == 1
    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_rss_publishes_the_text_of_an_html_entry_body() -> None:
    result, publish = await _sync_feed(_rss_item("<p>Hello <b>world</b></p>"))

    assert result.published == 1
    assert "# T\n\nHello  world\n" in publish.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_sync_rss_reads_an_entry_body_of_unterminated_tags_in_linear_time() -> None:
    # A tag pattern that rescans the rest of the body from every "<" takes ~20 s on 200,000 of them (a 200 KB feed),
    # all of it on the event loop; the bound is far above the linear cost and far below the quadratic one.
    run = "<" * 200_000 + " tail"
    started = time.perf_counter()

    result, publish = await _sync_feed(_rss_item(run))

    assert time.perf_counter() - started < 2.0
    assert result.published == 1
    assert run in publish.await_args.kwargs["content"]


@pytest.fixture
def origin() -> Iterator[LoopbackOrigin]:
    server = LoopbackOrigin()
    yield server
    server.close()


@pytest.fixture(autouse=True)
def _public_addresses_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYRM_ENABLE_SSRF_SHIELD", "true")
    monkeypatch.delenv("MYRM_ALLOWED_INTERNAL_HOSTS", raising=False)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "2130706433"])
@pytest.mark.asyncio
async def test_a_feed_on_an_internal_address_is_refused_and_never_requested(origin: LoopbackOrigin, host: str) -> None:
    origin.routes["/feed"] = page(_rss_item("secret"), "application/rss+xml")
    url = origin.url("/feed", host=host)

    result, publish = await _sync_urls([url])

    assert (result.failed, result.published) == (1, 0)
    assert result.errors == [f"{url}: {_UNREACHABLE}"]
    assert origin.hits == []
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refused_feed_and_a_dead_one_fail_with_the_same_text(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{probe.getsockname()[1]}/feed"
    monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")

    allowed_but_dead, _ = await _sync_urls([dead])
    monkeypatch.delenv("MYRM_ALLOWED_INTERNAL_HOSTS")
    refused, _ = await _sync_urls([dead])

    assert [error.split(": ", 1)[1] for error in allowed_but_dead.errors] == [_UNREACHABLE]
    assert [error.split(": ", 1)[1] for error in refused.errors] == [_UNREACHABLE]


@pytest.mark.asyncio
async def test_a_feed_on_a_host_the_operator_allows_is_published(origin: LoopbackOrigin, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")
    origin.routes["/feed"] = page(_rss_item("<p>Hello</p>"), "application/rss+xml")

    result, publish = await _sync_urls([origin.url("/feed")])

    assert (result.published, result.failed) == (1, 0)
    assert "# T\n\nHello\n" in publish.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_a_redirect_to_an_internal_host_is_refused(origin: LoopbackOrigin, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")
    origin.routes["/feed"] = redirect(origin.url("/secret", host="[::ffff:127.0.0.1]"))
    origin.routes["/secret"] = page(_rss_item("secret"))
    url = origin.url("/feed")

    result, _ = await _sync_urls([url])

    assert result.errors == [f"{url}: {_UNREACHABLE}"]
    assert origin.hits == ["/feed"]


@pytest.mark.asyncio
async def test_a_feed_past_the_size_cap_fails_instead_of_publishing_part_of_it(
    origin: LoopbackOrigin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")
    monkeypatch.setattr(rss, "_MAX_FEED_BYTES", 1024)
    origin.routes["/feed"] = page(_rss_item("x" * 2048), "application/rss+xml")
    url = origin.url("/feed")

    result, publish = await _sync_urls([url])

    assert (result.published, result.failed) == (0, 1)
    assert result.errors == [f"{url}: the response is too large"]
    publish.assert_not_awaited()
