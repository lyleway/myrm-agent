"""A PDF or Office file's parsed text is cut where the caller says; a caller that says nothing keeps the router default."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.documents import Document

from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.fetchers.protocols import FetchResult
from myrm_agent_harness.toolkits.web_fetch.processing.binary_router import MAX_PARSED_CHARS, route_binary_content
from myrm_agent_harness.toolkits.web_fetch.processing.pipeline import SOURCE_TRUNCATED_KEY
from tests.toolkits.web_fetch._degradation_support import stub_fetchers

PARSED = 150_000
URL = "https://example.com/r.pdf"


def _parser_yielding(chars: int) -> AsyncMock:
    parser = AsyncMock()
    parser.parse = AsyncMock(return_value="x" * chars)
    return parser


async def _route(**kwargs: int) -> Document | None:
    with patch("myrm_agent_harness.toolkits.file_parsers.get_parser", return_value=_parser_yielding(PARSED)):
        return await route_binary_content(
            b"%PDF-1.4 fake pdf", {"content-type": "application/pdf"}, "https://a.example/r.pdf", **kwargs
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "kept", "cut"),
    [
        ({}, MAX_PARSED_CHARS, True),
        ({"max_chars": PARSED}, PARSED, False),
        ({"max_chars": PARSED - 1}, PARSED - 1, True),
    ],
    ids=["default", "cap_at_length", "cap_below_length"],
)
async def test_the_router_cuts_parsed_text_at_the_callers_cap(kwargs: dict[str, int], kept: int, cut: bool) -> None:
    document = await _route(**kwargs)

    assert document is not None
    assert len(document.page_content) == kept
    assert (SOURCE_TRUNCATED_KEY in document.metadata) is cut


@pytest.mark.asyncio
async def test_a_crawl_hands_its_cap_to_the_parser_of_the_file_it_fetches() -> None:
    engine = FetchEngine(allow_private_networks=True)
    stub_fetchers(engine).fetch.return_value = FetchResult(
        html="", url=URL, raw_body=b"%PDF-1.4 fake pdf", headers={"content-type": "application/pdf"}
    )

    with patch("myrm_agent_harness.toolkits.file_parsers.get_parser", return_value=_parser_yielding(PARSED)):
        default = await engine.crawl(URL)
        capped_batch, failed = await engine.crawl_many([URL], max_parsed_chars=PARSED)

    assert default is not None
    assert len(default.page_content) == MAX_PARSED_CHARS
    assert default.metadata[SOURCE_TRUNCATED_KEY] is True
    assert not failed
    ((_, capped),) = capped_batch
    assert len(capped.page_content) == PARSED
    assert SOURCE_TRUNCATED_KEY not in capped.metadata
    await engine.shutdown()


@pytest.mark.asyncio
async def test_each_cap_has_its_own_cache_entry_and_callers_without_one_share_theirs() -> None:
    engine = FetchEngine(allow_private_networks=True)

    async def network(_url: str, **kwargs: int | None) -> tuple[Document, None]:
        cap = kwargs["max_parsed_chars"]
        return Document(page_content="x" * (cap or 10), metadata={}), None

    with patch.object(engine, "_fetch_from_network", new_callable=AsyncMock, side_effect=network) as fetch:
        results = [
            await engine.crawl(URL),
            await engine.crawl(URL, max_parsed_chars=1_000),
            await engine.crawl(URL),
            await engine.crawl(URL, max_parsed_chars=1_000),
        ]

    assert fetch.await_count == 2
    assert [len(doc.page_content) for doc in results if doc is not None] == [10, 1_000, 10, 1_000]
    await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["\n5", "\r\n5", "\t5", "#\n5"])
async def test_no_url_can_reach_the_cache_entry_of_a_capped_request(spelling: str) -> None:
    engine = FetchEngine(allow_private_networks=True)

    async def network(_url: str, **kwargs: int | None) -> tuple[Document, None]:
        return Document(page_content=f"cap={kwargs['max_parsed_chars']}", metadata={}), None

    with patch.object(engine, "_fetch_from_network", new_callable=AsyncMock, side_effect=network) as fetch:
        capped = await engine.crawl(URL, max_parsed_chars=5)
        lookalike = await engine.crawl(URL + spelling)

    assert fetch.await_count == 2
    assert capped is not None
    assert capped.page_content == "cap=5"
    assert lookalike is not None
    assert lookalike.page_content == "cap=None"
    await engine.shutdown()


@pytest.mark.asyncio
async def test_a_failed_url_is_remembered_for_every_cap() -> None:
    engine = FetchEngine(allow_private_networks=True)

    with patch.object(engine, "_fetch_from_network", new_callable=AsyncMock, return_value=(None, None)) as fetch:
        assert await engine.crawl(URL, max_parsed_chars=1_000) is None
        assert await engine.crawl(URL) is None
        assert await engine.crawl(URL, max_parsed_chars=2_000) is None

    assert fetch.await_count == 1
    await engine.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [None, 10], ids=["default", "capped"])
async def test_a_forced_refresh_clears_what_was_remembered_of_the_url_under_any_cap(cap: int | None) -> None:
    engine = FetchEngine(allow_private_networks=True)
    with patch.object(engine, "_fetch_from_network", new_callable=AsyncMock, return_value=(None, None)):
        assert await engine.crawl(URL, max_parsed_chars=cap) is None
    engine._record_failure_reason(URL, "not_found", "gone")

    async def network(_url: str, **_kwargs: int | None) -> tuple[Document, None]:
        return Document(page_content="ok", metadata={}), None

    with patch.object(engine, "_fetch_from_network", new_callable=AsyncMock, side_effect=network) as fetch:
        refreshed = await engine.crawl(URL, force_refresh=True, max_parsed_chars=cap)

    assert refreshed is not None
    assert fetch.await_count == 1
    assert engine.get_failure_reason(URL) is None
    await engine.shutdown()


@pytest.mark.asyncio
async def test_concurrent_callers_coalesce_only_with_callers_of_the_same_cap() -> None:
    engine = FetchEngine(allow_private_networks=True)
    release = asyncio.Event()

    async def network(_url: str, **kwargs: int | None) -> tuple[Document, None]:
        await release.wait()
        return Document(page_content=f"cap={kwargs['max_parsed_chars']}", metadata={}), None

    with patch.object(engine, "_fetch_from_network", new_callable=AsyncMock, side_effect=network) as fetch:
        calls = [
            asyncio.create_task(engine.crawl(URL)),
            asyncio.create_task(engine.crawl(URL, max_parsed_chars=1_000)),
            asyncio.create_task(engine.crawl(URL)),
        ]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(*calls)

    assert fetch.await_count == 2
    assert [doc.page_content for doc in results if doc is not None] == ["cap=None", "cap=1000", "cap=None"]
    await engine.shutdown()
