"""Shared builders for the FetchEngine HTTP/L4 test modules."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from langchain_core.documents import Document

from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.escalation.protocols import EscalationFetchResult
from myrm_agent_harness.toolkits.web_fetch.fetchers.protocols import FetchResult


class StubProvider:
    """A remote escalation provider whose ``fetch_url`` is an AsyncMock that returns ``result`` or raises ``error``."""

    def __init__(
        self, provider_id: str, result: EscalationFetchResult | None = None, *, error: Exception | None = None
    ) -> None:
        self.provider_id = provider_id
        self.fetch_url = AsyncMock(return_value=result, side_effect=error)


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


def pipeline_double(engine: FetchEngine) -> MagicMock:
    """The pipeline double that ``stub_fetchers`` installed on ``engine``."""
    pipeline = engine._pipeline
    assert isinstance(pipeline, MagicMock)
    return pipeline


def make_document(text: str = "content") -> Document:
    return Document(page_content=text, metadata={"title": "page"})


RICH_HTML = "<html><body><article><p>" + ("lorem ipsum content " * 40) + "</p></article></body></html>"
