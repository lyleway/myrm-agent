"""Tests for R26 ⑤ L4 escalation response content validation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from myrm_agent_harness.toolkits.web_fetch.engine.base import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.escalation.content_validator import is_valid_escalation_content
from myrm_agent_harness.toolkits.web_fetch.escalation.protocols import EscalationFetchResult


@pytest.mark.parametrize(
    ("title", "content"),
    [
        ("", ""),
        ("", "   \n\t  "),
        (
            "",
            "Title: Blocked\nURL Source: https://example.com\nWarning: Target URL returned error 403: Forbidden\n\nForbidden",
        ),
        ("", "Warning: Target URL returned error 429: Too Many Requests\nRate limited"),
        ("", "Warning: Target URL returned error 503: Service Unavailable\nUnavailable"),
        ("", "Warning: This page maybe not yet fully loaded\nLoading..."),
        ("百度安全验证", "请完成安全验证后继续访问"),
        ("安全验证", "请拖动滑块完成验证"),
        ("Just a moment...", "Checking your browser before accessing example.com."),
        ("Sign in to continue", "Please sign in with your account to view this page."),
        ("Continue shopping", "Click the button below to continue shopping."),
        ("Robot or human?", "Enter the characters you see below."),
        ("Access Denied", "You don't have permission to access this resource."),
        ("", "Title: 403 Forbidden\nURL Source: https://example.com\n\n# 403 Forbidden\nnginx"),
    ],
)
def test_rejects_non_content_block_and_error_pages(title: str, content: str) -> None:
    valid, reason = is_valid_escalation_content(content, title=title)
    assert valid is False
    assert reason != "ok"


def test_accepts_substantive_article_mentioning_block_keywords() -> None:
    body = (
        "# Debugging 403 Forbidden and Access Denied Errors\n\n"
        + (
            "This technical guide explains how reverse proxies, TLS termination, and IAM policies "
            "produce HTTP 403 responses and how to diagnose them safely in production clusters. "
        )
        * 6
    )
    valid, reason = is_valid_escalation_content(body, title="Access Denied Troubleshooting Guide")
    assert valid is True
    assert reason == "ok"


def test_accepts_short_clean_real_page() -> None:
    valid, reason = is_valid_escalation_content(
        "Example Domain\n\nThis domain is for use in illustrative examples in documents.",
        title="Example Domain",
    )
    assert valid is True
    assert reason == "ok"


@pytest.mark.asyncio
async def test_escalation_mixin_skips_challenge_page_and_uses_next_provider() -> None:
    bad_provider = AsyncMock()
    bad_provider.provider_id = "jina"
    bad_provider.fetch_url = AsyncMock(
        return_value=EscalationFetchResult(
            url="https://example.com/article",
            content="Warning: Target URL returned error 403: Forbidden\nAccess denied",
            title="403 Forbidden",
            provider_id="jina",
            is_markdown=True,
        )
    )

    good_provider = AsyncMock()
    good_provider.provider_id = "firecrawl"
    good_provider.fetch_url = AsyncMock(
        return_value=EscalationFetchResult(
            url="https://example.com/article",
            content="# Real Article\n\n" + ("Substantive technical content. " * 25),
            title="Real Article",
            provider_id="firecrawl",
            is_markdown=True,
        )
    )

    engine = FetchEngine()
    engine._escalation_providers = [bad_provider, good_provider]
    doc, _ = await engine._try_escalation("https://example.com/article")
    assert doc is not None
    assert doc.metadata["escalation_provider"] == "firecrawl"
    assert "Real Article" in doc.page_content
    bad_provider.fetch_url.assert_awaited_once()
    good_provider.fetch_url.assert_awaited_once()
