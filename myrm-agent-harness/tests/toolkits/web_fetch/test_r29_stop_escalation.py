"""Tests for R29: stop-escalation boundary (blocked vs needs_javascript), protocol signals, and stop-text guidance."""

from __future__ import annotations

import pytest

from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.fetchers.protocols import FetcherType, FetchResult
from myrm_agent_harness.toolkits.web_fetch.processing.antibot_detector import classify_block, is_blocked
from myrm_agent_harness.toolkits.web_fetch.processing.full_content_delivery import (
    FetchedPage,
    all_failed_content,
    plan_full_content,
)
from tests.toolkits.web_fetch._degradation_support import RICH_HTML, stub_fetchers

_UNTITLED_SCRIPT_SHELL = (
    "<!DOCTYPE html><html><head></head><body>"
    "<script>window.a=1;</script><script>window.b=2;</script><script>window.c=3;</script>"
    "<p>Verifying your browser...</p></body></html>"
)


class TestClassifyBlockProtocolSignals:
    def test_403_and_429_classify_as_blocked(self) -> None:
        assert classify_block(403, RICH_HTML)[0] == "blocked"
        assert classify_block(429, RICH_HTML)[0] == "blocked"

    @pytest.mark.parametrize(
        ("headers", "expected_substr"),
        [
            ({"CF-Mitigated": "challenge"}, "cf-mitigated"),
            ({"cf-mitigated": "challenge"}, "cf-mitigated"),
            ({"X-Amzn-Waf-Action": "challenge"}, "x-amzn-waf-action"),
            ({"x-amzn-waf-action": "challenge"}, "x-amzn-waf-action"),
        ],
    )
    def test_waf_challenge_headers_classify_as_blocked(
        self, headers: dict[str, str], expected_substr: str
    ) -> None:
        kind, reason = classify_block(200, RICH_HTML, headers=headers)
        assert kind == "blocked"
        assert expected_substr in reason
        assert is_blocked(200, RICH_HTML, headers=headers)[0] is True

    def test_202_with_script_shell_classifies_as_blocked_while_202_with_content_passes(self) -> None:
        assert classify_block(202, _UNTITLED_SCRIPT_SHELL)[0] == "blocked"
        assert classify_block(202, "<html></html>")[0] == "blocked"
        assert classify_block(202, RICH_HTML) == (None, "")

    def test_200_with_empty_or_script_shell_classifies_as_needs_javascript(self) -> None:
        assert classify_block(200, _UNTITLED_SCRIPT_SHELL)[0] == "needs_javascript"
        assert classify_block(200, "<html></html>")[0] == "needs_javascript"


class TestFetchEngineFailureReasonPropagation:
    async def test_records_blocked_for_short_403_and_challenge_headers(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)

        http.fetch.return_value = FetchResult(
            html="<html>Forbidden</html>",
            url="https://blocked.example/a",
            status_code=403,
            headers={},
            fetcher_type=FetcherType.HTTP,
        )
        assert await engine.crawl("https://blocked.example/a", allow_escalation=False) is None
        reason_a = engine.get_failure_reason("https://blocked.example/a")
        assert reason_a is not None
        assert reason_a[0] == "blocked"

        http.fetch.return_value = FetchResult(
            html=RICH_HTML,
            url="https://blocked.example/b",
            status_code=200,
            headers={"cf-mitigated": "challenge"},
            fetcher_type=FetcherType.HTTP,
        )
        assert await engine.crawl("https://blocked.example/b", allow_escalation=False) is None
        reason_b = engine.get_failure_reason("https://blocked.example/b")
        assert reason_b is not None
        assert reason_b[0] == "blocked"
        assert "cf-mitigated" in reason_b[1]

        await engine.shutdown()

    async def test_records_needs_javascript_for_200_empty_shell(self) -> None:
        engine = FetchEngine()
        http = stub_fetchers(engine)
        http.fetch.return_value = FetchResult(
            html=_UNTITLED_SCRIPT_SHELL,
            url="https://spa.example/app",
            status_code=200,
            headers={"content-type": "text/html"},
            fetcher_type=FetcherType.HTTP,
        )

        assert await engine.crawl("https://spa.example/app", allow_escalation=False) is None
        reason = engine.get_failure_reason("https://spa.example/app")
        assert reason is not None
        assert reason[0] == "needs_javascript"
        await engine.shutdown()


class TestStopTextGuidance:
    def test_all_failed_content_emits_stop_guidance_when_blocked(self) -> None:
        text = all_failed_content(
            ["https://blocked.example/page"],
            failure_reasons={"https://blocked.example/page": ("blocked", "HTTP 403")},
        )
        assert "Do NOT retry" in text
        assert "curl" in text
        assert "proxies" in text
        assert "mirrors" in text

    def test_all_failed_content_emits_js_guidance_when_needs_javascript(self) -> None:
        text = all_failed_content(
            ["https://spa.example/app"],
            failure_reasons={"https://spa.example/app": ("needs_javascript", "empty shell")},
        )
        assert "JavaScript" in text
        assert "Do NOT retry" not in text

    def test_partial_failure_with_blocked_url_appends_blocked_notice(self) -> None:
        delivery = plan_full_content(
            [FetchedPage(url="https://ok.example/a", title="OK", body="hello world")],
            ["https://blocked.example/b"],
            failure_reasons={"https://blocked.example/b": ("blocked", "HTTP 403")},
        )
        assert "Do NOT retry" in delivery.blocked_notice
        assert "curl" in delivery.blocked_notice
