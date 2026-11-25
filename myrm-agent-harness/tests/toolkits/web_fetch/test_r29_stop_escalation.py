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
    def test_waf_challenge_headers_classify_as_blocked(self, headers: dict[str, str], expected_substr: str) -> None:
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

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    def test_plain_5xx_outage_classifies_as_error_page_not_explicit_block(self, status_code: int) -> None:
        html = f"<html><body><h1>{status_code} Upstream Error</h1></body></html>"
        kind, _ = classify_block(status_code, html)
        assert kind == "error_page"
        assert is_blocked(status_code, html)[0] is True

    def test_503_with_waf_marker_still_classifies_as_blocked(self) -> None:
        html = "<html><head><title>Just a moment</title></head><body>Checking your browser</body></html>"
        assert classify_block(503, html)[0] == "blocked"


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

    def test_all_failed_content_emits_default_guidance_for_error_page(self) -> None:
        text = all_failed_content(
            ["https://down.example/page"],
            failure_reasons={"https://down.example/page": ("error_page", "HTTP 502 with HTML content")},
        )
        assert "Do NOT retry" not in text
        assert "unavailable" in text

    def test_partial_failure_with_blocked_url_appends_blocked_notice(self) -> None:
        delivery = plan_full_content(
            [FetchedPage(url="https://ok.example/a", title="OK", body="hello world")],
            ["https://blocked.example/b"],
            failure_reasons={"https://blocked.example/b": ("blocked", "HTTP 403")},
        )
        assert "Do NOT retry" in delivery.blocked_notice
        assert "curl" in delivery.blocked_notice


@pytest.mark.integration
class TestR29LiveLoopbackEndToEnd:
    """Real loopback HTTP server + real web_fetch_tool (zero fetcher/detector mocks)."""

    @pytest.mark.asyncio
    async def test_live_web_fetch_tool_distinguishes_502_outage_from_503_waf_and_403(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from myrm_agent_harness.toolkits.web_fetch.web_fetch_agent_tools import create_web_fetch_tool

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/outage-502":
                    body = b"<html><head><title>502 Bad Gateway</title></head><body><h1>502 Bad Gateway</h1><p>nginx</p></body></html>"
                    self.send_response(502)
                elif self.path == "/waf-503":
                    body = b"<html><head><title>Just a moment</title></head><body>Checking your browser</body></html>"
                    self.send_response(503)
                else:
                    body = b"<html><head><title>403 Forbidden</title></head><body>Access Denied</body></html>"
                    self.send_response(403)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            tool = create_web_fetch_tool(allow_private_networks=True)

            outage_result = str(
                await tool.ainvoke({"urls": [f"http://127.0.0.1:{port}/outage-502"], "reason": "check 502 outage"})
            )
            assert "502 Bad Gateway" not in outage_result
            assert "Do NOT retry" not in outage_result
            assert "Could not retrieve content" in outage_result

            waf_503_result = str(
                await tool.ainvoke({"urls": [f"http://127.0.0.1:{port}/waf-503"], "reason": "check 503 waf"})
            )
            assert "Do NOT retry" in waf_503_result

            blocked_403_result = str(
                await tool.ainvoke({"urls": [f"http://127.0.0.1:{port}/blocked-403"], "reason": "check 403 block"})
            )
            assert "Do NOT retry" in blocked_403_result
        finally:
            server.shutdown()
            server.server_close()
