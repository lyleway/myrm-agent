"""Unit tests: what the fault shim's Python half makes of the requests a page logged, and how it talks to the page."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import cast, get_args

import pytest

from tests.e2e.config_sync_deadline.fault_shim import (
    FAULT_SHIM_JS,
    ConfigLog,
    ConfigRequest,
    FaultKind,
    arm_fault,
    install_fault_shim,
    read_log,
)
from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage

_PAGE = cast(McpPage, object())


class _Chrome:
    """A page that answers each script with the next prepared answer and records every script it was given."""

    def __init__(self, *answers: object) -> None:
        self._answers: Iterator[object] = iter(answers)
        self.expressions: list[str] = []

    def evaluate(self, page: object, expression: str, *, timeout_sec: float = 15.0, await_promise: bool = True) -> object:
        self.expressions.append(expression)
        return next(self._answers)


def _client(chrome: _Chrome) -> ChromeMcpClient:
    return cast(ChromeMcpClient, chrome)


def _request(
    *,
    method: str = "POST",
    path: str = "/api/v1/config/sync",
    fault: str = "pass",
    markers: tuple[str, ...] = (),
    started: float = 100.0,
    headers: float | None = None,
    body: float | None = None,
    status: int | None = None,
    aborted: float | None = None,
) -> ConfigRequest:
    return ConfigRequest(method, path, fault, markers, started, headers, body, status, aborted)


def _raw_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "method": "POST",
        "path": "/api/v1/config/sync",
        "fault": "pass",
        "markers": [],
        "startedAt": 100.0,
        "headersAt": None,
        "bodyAt": None,
        "status": None,
        "abortedAt": None,
    }
    return entry | overrides


def _snapshot(*entries: object, now: object = 5_000.0, origin: object = 1_700_000_000_000.0) -> dict[str, object]:
    return {"now": now, "timeOrigin": origin, "entries": list(entries)}


@pytest.mark.parametrize(
    ("method", "path", "is_sync"),
    [
        ("POST", "/api/v1/config/sync", True),
        ("GET", "/api/v1/config/sync", False),
        ("POST", "/api/v1/config/webFetchEscalation", False),
    ],
)
def test_only_a_post_to_config_sync_is_a_sync_write(method: str, path: str, is_sync: bool) -> None:
    assert _request(method=method, path=path).is_config_sync is is_sync


@pytest.mark.parametrize(
    ("request_", "settled"),
    [
        pytest.param(_request(aborted=150.0), True, id="aborted-by-the-app-before-any-answer"),
        pytest.param(_request(), False, id="no-answer-yet"),
        pytest.param(_request(headers=120.0, status=200), True, id="answered-whole"),
        pytest.param(_request(fault="slow_headers", headers=300.0, status=200), True, id="slow-headers-answered"),
        pytest.param(_request(fault="slow_body", headers=120.0, status=200), False, id="slow-body-not-yet-delivered"),
        pytest.param(_request(fault="slow_body", headers=120.0, body=200.0, status=200), True, id="slow-body-delivered"),
        pytest.param(_request(fault="stall_body", headers=120.0, status=200), False, id="stalled-body-still-open"),
        pytest.param(_request(fault="stall_body", headers=120.0, status=200, aborted=900.0), True, id="stalled-body-cancelled"),
    ],
)
def test_a_request_is_settled_once_it_was_answered_in_full_or_cancelled(request_: ConfigRequest, settled: bool) -> None:
    assert request_.is_settled is settled


def test_the_latest_event_of_a_request_is_the_last_moment_it_did_anything() -> None:
    assert _request().latest_event_ms == 100.0
    assert _request(headers=120.0, body=180.0).latest_event_ms == 180.0
    assert _request(headers=120.0, body=180.0, aborted=200.0).latest_event_ms == 200.0


def test_a_request_describes_what_became_of_it_relative_to_its_start() -> None:
    request = _request(
        fault="slow_body", markers=("jina-e2e-a1", "jina-e2e-b2"), started=1_000.0, headers=1_020.0, body=1_080.0, status=200
    )

    assert request.describe() == (
        "t=1000 POST /api/v1/config/sync fault=slow_body markers=jina-e2e-a1,jina-e2e-b2 status=200 "
        "headers=+20ms body=+80ms cancelled=-"
    )


def test_a_request_that_never_got_an_answer_describes_its_missing_moments_with_dashes() -> None:
    request = _request(method="GET", path="/api/v1/config/webFetchEscalation", started=50.0, aborted=10_050.0)

    assert request.describe() == (
        "t=50 GET /api/v1/config/webFetchEscalation fault=pass markers=- status=None headers=- body=- cancelled=+10000ms"
    )


def test_the_page_is_quiet_for_as_long_as_since_its_latest_event_once_every_request_has_ended() -> None:
    log = ConfigLog((_request(headers=120.0, status=200), _request(started=900.0, headers=950.0, status=200)), 6_000.0, 0.0)

    assert log.quiet_for_ms == 5_050.0


def test_the_page_is_not_quiet_while_a_request_is_in_flight() -> None:
    log = ConfigLog((_request(headers=120.0, status=200), _request(started=900.0)), 60_000.0, 0.0)

    assert log.quiet_for_ms == 0.0


def test_a_page_that_made_no_request_has_been_quiet_since_it_started() -> None:
    assert ConfigLog((), 2_500.0, 0.0).quiet_for_ms == 2_500.0


def test_a_page_moment_converts_to_epoch_milliseconds_through_the_time_origin() -> None:
    assert ConfigLog((), 0.0, 1_700_000_000_000.0).wall_ms(1_234.5) == 1_700_000_001_234.5


def test_installing_the_shim_runs_it_once_in_the_page() -> None:
    chrome = _Chrome({"installed": True, "reused": False})

    install_fault_shim(_client(chrome), _PAGE)

    assert chrome.expressions == [FAULT_SHIM_JS]


def test_installing_the_shim_again_on_a_page_that_has_it_is_fine() -> None:
    install_fault_shim(_client(_Chrome({"installed": True, "reused": True})), _PAGE)


@pytest.mark.parametrize("answer", [None, "installed", {"installed": False}, {"installed": 1}, {}])
def test_a_shim_the_page_did_not_report_as_installed_is_an_error(answer: object) -> None:
    with pytest.raises(RuntimeError, match="config-sync fault shim did not install"):
        install_fault_shim(_client(_Chrome(answer)), _PAGE)


def test_arming_a_fault_defaults_to_a_post_the_backend_still_takes_without_delay() -> None:
    chrome = _Chrome(True)

    arm_fault(_client(chrome), _PAGE, "hold_headers", "jina-e2e-a1")

    assert chrome.expressions == ['window.__cfgFault.arm("hold_headers", "jina-e2e-a1", 0, true, "POST")']


def test_arming_a_fault_passes_every_option_on_to_the_page() -> None:
    chrome = _Chrome(True)

    arm_fault(_client(chrome), _PAGE, "slow_body", "/config/webFetchEscalation", delay_ms=250, apply=False, method="GET")

    assert chrome.expressions == ['window.__cfgFault.arm("slow_body", "/config/webFetchEscalation", 250, false, "GET")']


def test_a_needle_is_written_as_a_json_string_so_no_character_of_it_can_leave_it() -> None:
    chrome = _Chrome(True)
    needle = "a\"b\\c');alert(1);//"

    arm_fault(_client(chrome), _PAGE, "stall_body", needle)

    prefix, suffix = 'window.__cfgFault.arm("stall_body", ', ', 0, true, "POST")'
    expression = chrome.expressions[0]
    assert expression.startswith(prefix)
    assert expression.endswith(suffix)
    assert json.loads(expression.removeprefix(prefix).removesuffix(suffix)) == needle


@pytest.mark.parametrize("answer", [None, False, "true"])
def test_arming_a_page_without_the_shim_says_the_page_was_probably_reloaded(answer: object) -> None:
    with pytest.raises(RuntimeError, match="shim is missing on the page"):
        arm_fault(_client(_Chrome(answer)), _PAGE, "hold_headers", "needle")


def test_reading_the_log_turns_the_snapshot_into_requests_on_the_page_clock() -> None:
    chrome = _Chrome(
        _snapshot(
            _raw_entry(markers=["jina-e2e-a1"], headersAt=120.0, status=200.0),
            _raw_entry(
                method="GET", path="/api/v1/config/webFetchEscalation", fault="hold_headers", startedAt=400, abortedAt=10_400
            ),
            now=12_000.0,
            origin=1_700_000_000_000.0,
        )
    )

    log = read_log(_client(chrome), _PAGE)

    assert chrome.expressions == ["window.__cfgFault.snapshot()"]
    assert (log.now_ms, log.time_origin_ms) == (12_000.0, 1_700_000_000_000.0)
    assert log.requests == (
        _request(markers=("jina-e2e-a1",), headers=120.0, status=200),
        _request(method="GET", path="/api/v1/config/webFetchEscalation", fault="hold_headers", started=400.0, aborted=10_400.0),
    )


def test_a_status_the_page_reports_as_a_flag_or_text_is_not_a_status() -> None:
    chrome = _Chrome(_snapshot(_raw_entry(status=True, headersAt="soon", bodyAt=False)))

    (request,) = read_log(_client(chrome), _PAGE).requests

    assert (request.status, request.headers_at_ms, request.body_at_ms) == (None, None, None)


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        "snapshot",
        {"now": 1.0, "timeOrigin": 2.0, "entries": "none"},
        {"timeOrigin": 2.0, "entries": []},
        {"now": 1.0, "entries": []},
        {"now": True, "timeOrigin": 2.0, "entries": []},
    ],
)
def test_a_snapshot_without_entries_or_page_clock_is_unreadable(snapshot: object) -> None:
    with pytest.raises(RuntimeError, match="config-sync fault shim snapshot is unreadable"):
        read_log(_client(_Chrome(snapshot)), _PAGE)


def test_a_logged_entry_that_is_not_an_object_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="config request entry is not an object"):
        read_log(_client(_Chrome(_snapshot("GET /api/v1/config"))), _PAGE)


@pytest.mark.parametrize(
    "entry", [{"method": "GET", "markers": []}, _raw_entry(markers="jina-e2e-a1"), _raw_entry(startedAt="100")]
)
def test_a_logged_entry_without_a_start_time_or_a_marker_list_is_malformed(entry: dict[str, object]) -> None:
    with pytest.raises(RuntimeError, match="config request entry is malformed"):
        read_log(_client(_Chrome(_snapshot(entry))), _PAGE)


@pytest.mark.parametrize("kind", get_args(FaultKind))
def test_every_fault_kind_python_can_arm_has_an_implementation_in_the_shim_script(kind: str) -> None:
    assert f"    {kind}: " in FAULT_SHIM_JS
