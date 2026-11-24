"""Shared builders for the FetchEngine HTTP/L4 test modules."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from langchain_core.documents import Document

from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.fetchers.protocols import FetchResult


def http_result(html: str, status: int = 200) -> FetchResult:
    return FetchResult(html=html, url="http://example.com/page", status_code=status)


def mock_pipeline() -> MagicMock:
    """Pipeline double whose ``aprocess`` is awaitable, like the real conversion entry point."""
    pipeline = MagicMock()
    pipeline.aprocess = AsyncMock()
    return pipeline


def stub_fetchers(engine: FetchEngine) -> AsyncMock:
    http = AsyncMock()
    engine._http_fetcher = http  # type: ignore[assignment]
    engine._pipeline = mock_pipeline()  # type: ignore[assignment]
    return http


def make_document(text: str = "content") -> Document:
    return Document(page_content=text, metadata={"title": "page"})


RICH_HTML = "<html><body><article><p>" + ("lorem ipsum content " * 40) + "</p></article></body></html>"
