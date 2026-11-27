"""How ``TurnParser`` reads the CLIs' failure reports: ``error`` lines are notices and the ``result`` line judges.

It also tells a login the API keeps rejecting (an unbroken run of 401 retries) from a passing 401.
"""

from __future__ import annotations

import json

import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli.parser import error_text
from myrm_agent_harness.toolkits.acp.runtime.cli.turn_parser import TurnParser
from myrm_agent_harness.toolkits.acp.runtime.event_text import FAILURE_TEXT_CHARS, condense_failure_text

# The notice a CLI prints for a failed request it is about to retry; the verdict comes later.
_RETRY = "Request failed, retrying (2/5): stream disconnected"


def _feed(parser: TurnParser, data: dict[str, object]):
    return parser.feed(json.dumps(data))


class TestErrorText:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ({"error": {"message": "rate limited"}}, "rate limited"),
            ({"error": "something broke"}, "something broke"),
            ({"message": "at the top of the line"}, "at the top of the line"),
            ({"error": "", "message": "fallback"}, "fallback"),
            ({"error": None, "message": "from message"}, "from message"),
            ({"error": {"code": 7}}, "{'code': 7}"),
            ({"error": {}}, ""),
            ({}, ""),
        ],
    )
    def test_the_text_is_where_the_cli_put_it(self, data: dict[str, object], expected: str) -> None:
        assert error_text(data) == expected


class TestErrorLinesAreNotices:
    """A CLI can report every retry of a request as an ``error`` line, and a turn can recover from them."""

    def test_a_retry_notice_is_remembered_and_is_not_an_event(self) -> None:
        parser = TurnParser("s1")

        assert _feed(parser, {"type": "error", "message": _RETRY}) == []
        assert parser.notice == _RETRY

    @pytest.mark.parametrize(
        "line",
        [
            {"type": "error", "error": {"message": "nested object"}},
            {"type": "error", "error": "nested string"},
            {"type": "error", "message": "nested top-level text"},
        ],
    )
    def test_every_shape_of_an_error_line_is_a_notice(self, line: dict[str, object]) -> None:
        parser = TurnParser("s1")

        assert _feed(parser, line) == []
        assert parser.notice.startswith("nested ")

    def test_the_last_notice_is_the_one_kept(self) -> None:
        parser = TurnParser("s1")

        _feed(parser, {"type": "error", "message": "first"})
        _feed(parser, {"type": "error", "message": "second"})

        assert parser.notice == "second"

    def test_a_turn_without_error_lines_has_no_notice(self) -> None:
        parser = TurnParser("s1")

        _feed(parser, {"type": "usage", "input_tokens": 1, "output_tokens": 1})

        assert parser.notice == ""


class TestClaudeErrorCategory:
    """Claude names what went wrong on the synthetic message it prints before its ``result`` line."""

    @staticmethod
    def _error_message(category: str, text: str) -> dict[str, object]:
        message = {"model": "<synthetic>", "content": [{"type": "text", "text": text}]}
        return {"type": "assistant", "error": category, "message": message}

    def test_the_category_reaches_the_verdict_when_there_is_no_api_status(self) -> None:
        parser = TurnParser("s1")

        assert _feed(parser, self._error_message("authentication_failed", "Not logged in · Please run /login")) == []
        _feed(
            parser,
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "api_error_status": None,
                "result": "Not logged in · Please run /login",
            },
        )

        assert parser.result is not None
        assert parser.result.assistant_error == "authentication_failed"
        assert parser.result.api_error_status is None
        assert parser.result.message == "Not logged in · Please run /login"

    def test_the_last_category_wins(self) -> None:
        parser = TurnParser("s1")

        _feed(parser, self._error_message("rate_limit", "slow down"))
        _feed(parser, self._error_message("invalid_request", "bad"))
        _feed(parser, {"type": "result", "subtype": "success", "is_error": True, "result": "bad"})

        assert parser.result is not None
        assert parser.result.assistant_error == "invalid_request"

    def test_a_verdict_without_an_error_message_has_no_category(self) -> None:
        parser = TurnParser("s1")

        _feed(parser, {"type": "result", "subtype": "success", "is_error": False, "result": "done"})

        assert parser.result is not None
        assert parser.result.assistant_error == ""


_PAGE = (
    "<html>\r\n<head><title>403 Forbidden</title></head>\r\n<body><center><h1>403 Forbidden</h1></center>"
    "<hr><center>nginx</center></body>\r\n</html>\r\n"
)


class TestCondensedFailureText:
    """A failure text is shown to a person and logged, so it is one bounded line."""

    @pytest.mark.parametrize(
        "text",
        [
            "model not supported",
            "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header",
            'API Error: 400 {"type":"error"}',
            "x" * FAILURE_TEXT_CHARS,
        ],
    )
    def test_an_ordinary_message_passes_through_unchanged(self, text: str) -> None:
        assert condense_failure_text(text) == text

    def test_lines_and_runs_of_blanks_become_one_line(self) -> None:
        assert condense_failure_text("  first line\r\n\tsecond   line\n\n") == "first line second line"

    def test_a_quoted_web_page_shrinks_to_its_title(self) -> None:
        text = f"unexpected status 403 Forbidden: {_PAGE}, url: https://api.openai.com/v1/responses, cf-ray: abc"

        assert condense_failure_text(text) == (
            "unexpected status 403 Forbidden: [web page: 403 Forbidden] , url: https://api.openai.com/v1/responses, cf-ray: abc"
        )

    def test_a_doctype_page_without_a_title_is_just_a_web_page(self) -> None:
        text = "Error: <!DOCTYPE html><html><body><h1>Blocked</h1></body></html>"

        assert condense_failure_text(text) == "Error: [web page]"

    def test_a_page_that_is_cut_off_is_still_one_marker(self) -> None:
        text = "Error: <html><head><title>Just a moment...</title></head><body>" + "x" * 5000

        assert condense_failure_text(text) == "Error: [web page: Just a moment...]"

    def test_a_long_message_is_cut_with_an_ellipsis_at_the_bound(self) -> None:
        condensed = condense_failure_text("e" * 5000)

        assert len(condensed) == FAILURE_TEXT_CHARS
        assert condensed.endswith("\u2026")

    def test_empty_text_stays_empty(self) -> None:
        assert condense_failure_text(" \n\t ") == ""

    def test_error_lines_notices_and_verdicts_are_all_condensed(self) -> None:
        parser = TurnParser("s1")

        _feed(parser, {"type": "error", "message": f"unexpected status 403 Forbidden: {_PAGE}"})
        assert parser.notice == "unexpected status 403 Forbidden: [web page: 403 Forbidden]"

        _feed(parser, {"type": "result", "subtype": "success", "is_error": True, "result": "r" * 5000})
        assert parser.result is not None
        assert len(parser.result.message) == FAILURE_TEXT_CHARS


def _api_retry(attempt: int, status: object = 401) -> dict[str, object]:
    """The ``system/api_retry`` line claude 2.1.199 prints before each retry of a failed request."""
    return {
        "type": "system",
        "subtype": "api_retry",
        "attempt": attempt,
        "max_retries": 10,
        "retry_delay_ms": 547.9,
        "error_status": status,
        "error": "authentication_failed" if status == 401 else "server_error",
        "session_id": "cli-1",
    }


class TestRejectedLogin:
    """Claude retries a 401 for minutes; the parser says when the retries show the login is rejected."""

    def test_three_401_retries_in_a_row_are_a_rejected_login(self) -> None:
        parser = TurnParser("s1")

        for attempt in (1, 2):
            assert _feed(parser, _api_retry(attempt)) == []
            assert parser.auth_rejection == ""
        assert _feed(parser, _api_retry(3)) == []

        assert parser.auth_rejection == "API request rejected with HTTP 401 (authentication_failed)"

    def test_another_status_starts_the_count_again(self) -> None:
        parser = TurnParser("s1")

        for attempt, status in enumerate((401, 401, 529, 401, 401), start=1):
            _feed(parser, _api_retry(attempt, status))
        assert parser.auth_rejection == ""

        _feed(parser, _api_retry(6))
        assert parser.auth_rejection != ""

    @pytest.mark.parametrize("status", [None, 403, 429, 500, "401"], ids=["none", "403", "429", "500", "string"])
    def test_only_a_401_status_counts(self, status: object) -> None:
        parser = TurnParser("s1")

        for attempt in range(1, 8):
            _feed(parser, _api_retry(attempt, status))

        assert parser.auth_rejection == ""

    def test_anything_the_turn_produces_starts_the_count_again(self) -> None:
        parser = TurnParser("s1")

        _feed(parser, _api_retry(1))
        _feed(parser, _api_retry(2))
        _feed(parser, {"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "hi"}]}})
        _feed(parser, _api_retry(1))

        assert parser.auth_rejection == ""

    def test_lines_without_events_do_not_break_the_run(self) -> None:
        parser = TurnParser("s1")

        _feed(parser, _api_retry(1))
        _feed(parser, {"type": "system", "subtype": "hook_started"})
        _feed(parser, _api_retry(2))
        parser.feed("")
        _feed(parser, _api_retry(3))

        assert parser.auth_rejection != ""
