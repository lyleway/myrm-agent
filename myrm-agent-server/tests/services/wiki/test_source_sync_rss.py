"""Unit tests for RSS wiki source sync."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.wiki.source_sync.rss import sync_rss_feeds_to_wiki
from app.services.wiki.source_sync.schemas import WikiSourceSyncResult


async def _sync_feed(body: str) -> tuple[WikiSourceSyncResult, AsyncMock]:
    """Sync one feed whose server answers with ``body``; returns the run result and the mocked publish call."""
    publish = AsyncMock(return_value=MagicMock(written=True, skipped=False, conflict_skipped=False, security_blocked=False))
    with patch("app.services.wiki.source_sync.rss.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        response = MagicMock()
        response.text = body
        response.raise_for_status = MagicMock()
        client.get = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client_cls.return_value = client
        with patch("app.services.wiki.source_sync.rss.publish_source_markdown", publish):
            result = await sync_rss_feeds_to_wiki(
                MagicMock(),
                feed_urls=["https://example.com/feed.xml"],
                max_items=5,
                auto_compile=False,
                compiler_enqueue=None,
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
