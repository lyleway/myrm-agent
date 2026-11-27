"""Unit tests: the card session follows one edit from the keyboard to the database through a page and a backend that are faked."""

from __future__ import annotations

import json
import re
from typing import cast

import pytest

from tests.e2e.config_sync_deadline import card_session
from tests.e2e.config_sync_deadline.card_session import CONFIG_KEY, QUIET_MS, CardSession, jina_key, run_marker
from tests.e2e.config_sync_deadline.fault_shim import FAULT_SHIM_JS
from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage

_API = "http://api.test"
_PAGE = cast(McpPage, object())
_SNAPSHOT_SCRIPT = "window.__cfgFault.snapshot()"
_HISTORY_URL = f"{_API}/api/v1/config/{CONFIG_KEY}/history?limit=100"


class _Chrome:
    """A page that answers each script by what it is for (a needle found in it) and records every script it was given."""

    def __init__(self, answers: dict[str, object] | None = None, snapshots: list[dict[str, object]] | None = None) -> None:
        self.answers = answers or {}
        self.snapshots = list(snapshots or [])
        self.pages: list[object] = []
        self.scripts: list[tuple[str, float]] = []

    def evaluate(self, page: object, expression: str, *, timeout_sec: float = 15.0, await_promise: bool = True) -> object:
        self.pages.append(page)
        self.scripts.append((expression, timeout_sec))
        if expression == _SNAPSHOT_SCRIPT:
            return self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
        return next((answer for needle, answer in self.answers.items() if needle in expression), None)


class _Clock:
    """Stands in for ``time``: sleeping moves the clock, so polling loops end at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _session(chrome: _Chrome) -> CardSession:
    return CardSession(cast(ChromeMcpClient, chrome), _PAGE, _API)


def _entry(
    marker: str = "jina-e2e-a1",
    *,
    method: str = "POST",
    path: str = "/api/v1/config/sync",
    started: float = 100.0,
    headers: float | None = 120.0,
    status: int | None = 200,
) -> dict[str, object]:
    return {
        "method": method,
        "path": path,
        "fault": "pass",
        "markers": [marker],
        "startedAt": started,
        "headersAt": headers,
        "bodyAt": None,
        "status": status,
        "abortedAt": None,
    }


def _snapshot(*entries: dict[str, object], now: float = 10_000.0) -> dict[str, object]:
    return {"now": now, "timeOrigin": 1_700_000_000_000.0, "entries": list(entries)}


@pytest.fixture(autouse=True)
def _progress(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The wall-clock progress the session reports to the E2E orchestrator, by node name."""
    nodes: list[str] = []
    monkeypatch.setattr(card_session, "touch_wall_progress", lambda *, current_node: nodes.append(current_node))
    return nodes


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(card_session, "time", fake)
    return fake


def _serve_config(monkeypatch: pytest.MonkeyPatch, *values: object) -> None:
    """The backend's stored value of the key: one answer per read, the last one repeated."""
    pending = list(values)

    def fetch_config_value(key: str, *, api_url: str) -> object:
        assert (key, api_url) == (CONFIG_KEY, _API)
        return pending.pop(0) if len(pending) > 1 else pending[0]

    monkeypatch.setattr(card_session, "fetch_config_value", fetch_config_value)


def _serve_history(monkeypatch: pytest.MonkeyPatch, history: object) -> list[tuple[str, str]]:
    """The backend's audit log of the key; returns the requests made to it."""
    requested: list[tuple[str, str]] = []

    def http_json(method: str, url: str) -> object:
        requested.append((method, url))
        return history

    monkeypatch.setattr(card_session, "http_json", http_json)
    return requested


def test_a_run_marker_carries_its_step_and_is_the_same_for_the_whole_run() -> None:
    assert run_marker("s1") == run_marker("s1")
    assert run_marker("s1") != run_marker("s2")
    assert run_marker("s1").startswith("jina-e2e-")
    assert run_marker("s1").endswith("s1")


def test_a_run_marker_is_what_the_shim_script_lists_as_a_marker() -> None:
    pattern = re.search(r"const MARKERS = /(.+?)/g;", FAULT_SHIM_JS)
    assert pattern, "the shim script no longer declares its marker pattern"

    assert re.fullmatch(pattern.group(1), run_marker("a1b2"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"jina_api_key": "stored"}, "stored"),
        ({"jinaApiKey": "sent"}, "sent"),
        ({"jina_api_key": "", "jinaApiKey": "sent"}, "sent"),
        ({"firecrawl": "x"}, None),
        (None, None),
        ("jina", None),
    ],
)
def test_the_jina_key_of_a_value_is_read_from_its_backend_or_app_spelling(value: object, expected: object) -> None:
    assert jina_key(value) == expected


def test_opening_the_card_clears_modals_waits_for_it_expanded_and_returns_a_session_on_that_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps: list[tuple[object, ...]] = []
    chrome = _Chrome()
    monkeypatch.setattr(card_session, "dismiss_blocking_modals", lambda client, page: steps.append(("dismiss", client, page)))
    monkeypatch.setattr(card_session, "_warm_ui_parallel_wait_sec", lambda seconds: seconds * 2)

    def wait_for_state(client: object, page: object, script: str, *, timeout_sec: float) -> dict[str, object]:
        steps.append(("wait", client, page, "card_missing" in script, timeout_sec))
        return {"ready": True, "jina": ""}

    monkeypatch.setattr(card_session, "wait_for_state", wait_for_state)

    opened = CardSession.open(cast(ChromeMcpClient, chrome), _PAGE, _API)

    assert steps == [("dismiss", chrome, _PAGE), ("wait", chrome, _PAGE, True, 240.0)]
    assert opened == _session(chrome)


def test_a_card_that_never_got_ready_fails_the_open_with_the_state_the_page_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card_session, "dismiss_blocking_modals", lambda client, page: None)
    monkeypatch.setattr(card_session, "_warm_ui_parallel_wait_sec", lambda seconds: seconds)
    monkeypatch.setattr(
        card_session, "wait_for_state", lambda *_args, **_kwargs: {"ready": False, "reason": "jina_input_missing"}
    )

    with pytest.raises(AssertionError, match="jina_input_missing"):
        CardSession.open(cast(ChromeMcpClient, _Chrome()), _PAGE, _API)


def test_editing_types_the_value_into_the_card_as_a_json_string() -> None:
    chrome = _Chrome({"return typeKey(value)": True})
    value = 'jina-e2e-a1"\\'

    _session(chrome).edit(value)

    [(expression, _timeout)] = chrome.scripts
    assert expression.endswith(f")({json.dumps(value)})")
    assert 'input[type="password"]' in expression


def test_editing_a_card_without_its_key_input_fails() -> None:
    with pytest.raises(AssertionError, match="Jina key input is missing"):
        _session(_Chrome({"return typeKey(value)": False})).edit("jina-e2e-a1")


def test_editing_twice_while_the_first_write_is_held_returns_the_page_time_of_the_second_edit(_progress: list[str]) -> None:
    chrome = _Chrome({"thenArm": {"ok": True, "newerAt": 1_234}})

    newer_at = _session(chrome).edit_while_held("jina-e2e-old", "jina-e2e-new")

    assert newer_at == 1_234.0
    [(expression, timeout)] = chrome.scripts
    assert timeout == 60.0
    assert expression.endswith('("jina-e2e-old", "jina-e2e-new", null)')
    assert _progress == ["config_sync_edit_while_held"]


def test_editing_while_held_can_arm_a_stall_of_the_settlement_read_in_the_same_page_turn() -> None:
    chrome = _Chrome({"thenArm": {"ok": True, "newerAt": 5.5}})

    _session(chrome).edit_while_held("jina-e2e-old", "jina-e2e-new", hold_settlement_read=True)

    [(expression, _timeout)] = chrome.scripts
    assert expression.endswith(
        '("jina-e2e-old", "jina-e2e-new", '
        f'{{"kind": "hold_headers", "needle": "/config/{CONFIG_KEY}", "method": "GET", "apply": false}})'
    )


@pytest.mark.parametrize(
    "result",
    [
        {"ok": False, "reason": "held_request_never_started"},
        {"ok": True},
        {"ok": True, "newerAt": "soon"},
        None,
    ],
)
def test_a_second_edit_the_page_did_not_report_is_an_error(result: object) -> None:
    with pytest.raises(AssertionError):
        _session(_Chrome({"thenArm": result})).edit_while_held("jina-e2e-old", "jina-e2e-new")


def test_the_card_shows_the_key_its_input_holds_and_the_state_of_its_switch() -> None:
    chrome = _Chrome({"input.value : null": "jina-e2e-a1", "aria-checked": "true"})
    session = _session(chrome)

    assert session.shown() == "jina-e2e-a1"
    assert session.enabled_switch() == "true"
    assert chrome.pages == [_PAGE, _PAGE]


def test_the_stored_key_is_the_one_in_the_backends_value_of_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_config(monkeypatch, {"jina_api_key": "jina-e2e-a1"})

    assert _session(_Chrome()).stored() == "jina-e2e-a1"


def test_the_applied_versions_of_a_marker_are_those_of_the_audit_rows_that_hold_it(monkeypatch: pytest.MonkeyPatch) -> None:
    requested = _serve_history(
        monkeypatch,
        [
            {"version": "1700_1", "new_value": {"jina_api_key": "jina-e2e-a1"}},
            {"version": "1701_2", "new_value": {"jinaApiKey": "jina-e2e-a1"}},
            {"version": "1702_0", "new_value": {"jina_api_key": "jina-e2e-b2"}},
            "not a row",
        ],
    )
    session = _session(_Chrome())

    assert session.applied_versions("jina-e2e-a1") == ["1700_1", "1701_2"]
    assert session.applied_count("jina-e2e-a1") == 2
    assert requested == [("GET", _HISTORY_URL)] * 2


def test_an_audit_log_that_is_not_a_list_fails_the_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_history(monkeypatch, {"detail": "unavailable"})

    with pytest.raises(AssertionError, match="unavailable"):
        _session(_Chrome()).applied_versions("jina-e2e-a1")


def test_the_moment_a_write_was_applied_is_the_epoch_millisecond_its_version_starts_with(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_history(monkeypatch, [{"version": "1700000123456_3", "new_value": {"jina_api_key": "jina-e2e-a1"}}])

    assert _session(_Chrome()).applied_at_ms("jina-e2e-a1") == 1_700_000_123_456


@pytest.mark.parametrize("applied", [0, 2])
def test_a_write_that_was_not_applied_exactly_once_has_no_moment_of_its_own(
    monkeypatch: pytest.MonkeyPatch, applied: int
) -> None:
    _serve_history(
        monkeypatch, [{"version": f"170000{n}_0", "new_value": {"jina_api_key": "jina-e2e-a1"}} for n in range(applied)]
    )

    with pytest.raises(AssertionError, match="must be applied exactly once"):
        _session(_Chrome()).applied_at_ms("jina-e2e-a1")


def test_waiting_for_the_stored_key_polls_until_the_backend_holds_the_value(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, _progress: list[str]
) -> None:
    _serve_config(monkeypatch, {}, {"jina_api_key": "old"}, {"jinaApiKey": "jina-e2e-a1"})

    _session(_Chrome()).wait_stored("jina-e2e-a1")

    assert clock.now == 1.0
    assert _progress == ["config_sync_wait_stored"] * 3


def test_a_key_the_backend_never_stores_fails_the_wait_with_what_it_holds(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    _serve_config(monkeypatch, {"jina_api_key": "old"})

    with pytest.raises(AssertionError, match=r"did not store 'jina-e2e-a1' within 2\.0s; it holds 'old'"):
        _session(_Chrome()).wait_stored("jina-e2e-a1", timeout_sec=2.0)

    assert clock.now == 2.0


def test_the_log_is_what_the_shim_recorded_on_the_page() -> None:
    chrome = _Chrome(snapshots=[_snapshot(_entry(), now=7_000.0)])

    log = _session(chrome).log()

    assert log.now_ms == 7_000.0
    assert [request.markers for request in log.requests] == [("jina-e2e-a1",)]


def test_the_writes_of_a_marker_are_the_sync_posts_that_carry_it() -> None:
    chrome = _Chrome(
        snapshots=[
            _snapshot(
                _entry("jina-e2e-a1"),
                _entry("jina-e2e-b2"),
                _entry("jina-e2e-a1", method="GET", path="/api/v1/config/sync"),
                _entry("jina-e2e-a1", path=f"/api/v1/config/{CONFIG_KEY}"),
            )
        ]
    )

    writes = _session(chrome).writes("jina-e2e-a1")

    assert [(write.method, write.path, write.markers) for write in writes] == [("POST", "/api/v1/config/sync", ("jina-e2e-a1",))]


def test_the_writes_of_a_marker_settle_once_all_have_ended_and_the_page_clock_has_been_quiet(
    clock: _Clock, _progress: list[str]
) -> None:
    latest_event = 120.0
    chrome = _Chrome(
        snapshots=[
            _snapshot(_entry(headers=None, status=None), now=1_000.0),
            _snapshot(_entry(), now=latest_event + QUIET_MS - 1),
            _snapshot(_entry(), now=latest_event + QUIET_MS),
        ]
    )

    writes = _session(chrome).settled_writes("jina-e2e-a1")

    assert [write.status for write in writes] == [200]
    assert clock.now == 1.0
    assert _progress == ["config_sync_settle"] * 3


def test_a_write_that_never_settles_fails_with_what_the_page_logged_for_it(clock: _Clock) -> None:
    chrome = _Chrome(snapshots=[_snapshot(_entry(headers=None, status=None))])

    with pytest.raises(
        AssertionError, match=r"writes of 'jina-e2e-a1' did not settle within 3\.0s: \[ConfigRequest\("
    ) as failure:
        _session(chrome).settled_writes("jina-e2e-a1", timeout_sec=3.0)

    assert clock.now == 3.0
    assert "status=None" in str(failure.value)


def test_the_settlement_reads_are_the_gets_of_the_key_made_since_the_second_edit() -> None:
    server_copy = f"/api/v1/config/{CONFIG_KEY}"
    chrome = _Chrome(
        snapshots=[
            _snapshot(
                _entry(method="GET", path=server_copy, started=50.0),
                _entry(method="GET", path=server_copy, started=200.0),
                _entry(method="POST", path=server_copy, started=300.0),
                _entry(method="GET", path="/api/v1/config/personalSettings", started=400.0),
                _entry(method="GET", path=f"{server_copy}/history", started=500.0),
            )
        ]
    )

    reads = _session(chrome).server_reads_after(100.0)

    assert [read.started_at_ms for read in reads] == [200.0]
