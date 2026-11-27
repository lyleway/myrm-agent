"""Unit tests: a scenario sets the stored config before its page opens and puts it back, and reports every failing phase."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import cast

import pytest

from tests.e2e.config_sync_deadline import scenario
from tests.e2e.config_sync_deadline.card_session import CONFIG_KEY, SETTINGS_ROUTE, CardSession
from tests.e2e.config_sync_deadline.fault_shim import ConfigLog, ConfigRequest
from tests.e2e.config_sync_deadline.scenario import DEFAULT_CONFIG, deadline_card, run_phases
from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage

_API = "http://api.test"
_CLIENT = cast(ChromeMcpClient, object())
_PAGE = cast(McpPage, object())
_CARD = cast(CardSession, object())
_STORED_BEFORE: dict[str, object] = {"jinaApiKey": "stored-before"}


def _put(value: dict[str, object]) -> str:
    return f"put {json.dumps(value, sort_keys=True)}"


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fakes everything a scenario touches; the returned list is the order in which it touched it."""
    order: list[str] = []

    @contextmanager
    def open_settings_subroute(route: str, *, timeout_ms: int) -> Iterator[tuple[ChromeMcpClient, McpPage]]:
        assert (route, timeout_ms) == (SETTINGS_ROUTE, 90_000)
        order.append("open page")
        try:
            yield _CLIENT, _PAGE
        finally:
            order.append("close page")

    def open_card(client: ChromeMcpClient, page: McpPage, api_base: str) -> CardSession:
        assert (client, page, api_base) == (_CLIENT, _PAGE, _API)
        order.append("open card")
        return _CARD

    def put_config_value(key: str, value: dict[str, object], *, api_url: str) -> None:
        assert (key, api_url) == (CONFIG_KEY, _API)
        order.append(_put(value))

    def wait_e2e_provider_ready(*, api_url: str) -> bool:
        assert api_url == _API
        order.append("provider ready")
        return True

    monkeypatch.setattr(scenario, "get_e2e_api_url", lambda: _API)
    monkeypatch.setattr(scenario, "wait_e2e_provider_ready", wait_e2e_provider_ready)
    monkeypatch.setattr(scenario, "fetch_config_value", lambda key, *, api_url: _STORED_BEFORE)
    monkeypatch.setattr(scenario, "put_config_value", put_config_value)
    monkeypatch.setattr(scenario, "warm_ui_route", lambda route: order.append(f"warm {route}"))
    monkeypatch.setattr(scenario, "open_settings_subroute", open_settings_subroute)
    monkeypatch.setattr(CardSession, "open", staticmethod(open_card))
    monkeypatch.setattr(scenario, "install_fault_shim", lambda client, page: order.append("install shim"))
    return order


def _puts(order: list[str]) -> list[dict[str, object]]:
    return [json.loads(entry.removeprefix("put ")) for entry in order if entry.startswith("put ")]


def test_the_page_opens_on_the_defaults_and_the_stored_value_comes_back_afterwards(calls: list[str]) -> None:
    with deadline_card() as card:
        assert card is _CARD
        calls.append("body")

    assert calls == [
        "provider ready",
        _put(DEFAULT_CONFIG),
        f"warm {SETTINGS_ROUTE}",
        "open page",
        "open card",
        "install shim",
        "body",
        "close page",
        _put(_STORED_BEFORE),
    ]


def test_a_scenario_can_start_from_a_value_of_its_own_without_changing_the_defaults(calls: list[str]) -> None:
    snapshot = json.loads(json.dumps(DEFAULT_CONFIG))

    with deadline_card({**DEFAULT_CONFIG, "enabled": True, "jinaApiKey": "stored-key"}):
        pass

    assert _puts(calls)[0] == {**DEFAULT_CONFIG, "enabled": True, "jinaApiKey": "stored-key"}
    assert DEFAULT_CONFIG == snapshot


def test_the_stored_value_comes_back_when_the_scenario_fails(calls: list[str]) -> None:
    with pytest.raises(AssertionError, match="the phase failed"), deadline_card():
        raise AssertionError("the phase failed")

    assert calls[-2:] == ["close page", _put(_STORED_BEFORE)]


def test_an_empty_stored_value_is_not_written_back(calls: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scenario, "fetch_config_value", lambda key, *, api_url: {})

    with deadline_card():
        pass

    assert len(_puts(calls)) == 1, "only the starting value is written; there is nothing to put back"


def test_a_backend_that_is_not_ready_ends_the_scenario_before_anything_is_written(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scenario, "wait_e2e_provider_ready", lambda *, api_url: False)

    with pytest.raises(pytest.fail.Exception, match="E2E backend not ready"), deadline_card():
        pytest.fail("the body must not run")

    assert calls == []


class _CardWithLog:
    def __init__(self, *requests: ConfigRequest) -> None:
        self._log = ConfigLog(requests, now_ms=0.0, time_origin_ms=0.0)

    def log(self) -> ConfigLog:
        return self._log


def _request(path: str) -> ConfigRequest:
    return ConfigRequest("POST", path, "pass", (), 5.0, None, None, None, None)


def _phase(evidence: dict[str, object]) -> Callable[[], dict[str, object]]:
    return lambda: evidence


def _failing(message: str) -> Callable[[], dict[str, object]]:
    def phase() -> dict[str, object]:
        raise AssertionError(message)

    return phase


def test_every_phase_runs_and_its_evidence_is_printed_with_how_long_it_took(capsys: pytest.CaptureFixture[str]) -> None:
    card = cast(CardSession, _CardWithLog())

    evidence = run_phases(card, {"first": _phase({"writes": 1}), "second": _phase({"retries": 2})})

    assert evidence == {"first": {"writes": 1, "took_s": 0}, "second": {"retries": 2, "took_s": 0}}
    assert capsys.readouterr().out == f"E2E_CONFIG_SYNC_DEADLINE: {json.dumps(evidence)}\n"


def test_a_failing_phase_does_not_hide_the_phases_after_it_and_the_failure_carries_the_page_requests(
    capsys: pytest.CaptureFixture[str],
) -> None:
    card = cast(CardSession, _CardWithLog(_request("/api/v1/config/sync")))

    with pytest.raises(AssertionError) as caught:
        run_phases(
            card,
            {
                "first": _failing("the first broke"),
                "second": _phase({"writes": 1}),
                "third": _failing("the third broke"),
            },
        )

    message = str(caught.value)
    assert "phase 'first': the first broke" in message and "phase 'third': the third broke" in message
    assert message.count("config requests of the page:\nt=5 POST /api/v1/config/sync") == 2
    printed = capsys.readouterr().out
    assert "E2E_CONFIG_SYNC_DEADLINE:" in printed and '"second"' in printed
    assert '"first"' not in printed and '"third"' not in printed
