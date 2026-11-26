"""Chrome E2E: chat-history search shows message times on the viewer's clock, and the Cmd+K time presets step by calendar
months (no LLM).

The prior-chat seed stamps its messages at a pinned instant and tags them with a one-off marker that each check searches
for, so a check holds or fails with the stack, not with the time of day it runs or the seeds earlier runs left behind:
- an instant that falls on a different UTC day than the viewer's day shows the viewer's day on the Cmd+K result card
  and the viewer's clock time on a shared-context history hit, and the search API hands the client that instant with an
  explicit UTC offset;
- at the end of a month the Cmd+K one-month preset reaches back to the same day of the previous month (Mar 31 -> Feb 28),
  so a message from the 1st is still inside it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from chrome_mcp.client import ChromeMcpClient, McpPage

from tests.support.chrome_mcp_e2e import (
    _warm_ui_parallel_wait_sec,
    dismiss_blocking_modals,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    open_mcp_page,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)

# Mirror SearchResultCard.formatDate and formatSharedContextDate, so each expectation is rendered by the same browser
# that renders the surface.
_DAY_OPTIONS = json.dumps({"month": "short", "day": "numeric", "year": "numeric"})
_CLOCK_OPTIONS = json.dumps({"month": "short", "day": "numeric", "hour": "2-digit", "minute": "2-digit"})

_SEARCH_INPUT = "[data-search-input]"
_PANEL_HISTORY_ROW = 'div[class*="grid-cols-[1fr_150px_auto]"]'

_CHAT_HOME_READY_JS = """(() => ({ ready: !!document.querySelector('[data-chat-input]') }))()"""

_OPEN_SEARCH_DIALOG_JS = """(() => {
  const trigger = document.querySelector('[data-search-trigger]');
  if (trigger instanceof HTMLElement) {
    trigger.click();
  } else {
    window.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'k', code: 'KeyK', metaKey: true, ctrlKey: true, bubbles: true, cancelable: true }),
    );
  }
  return { ok: true, hasTrigger: Boolean(trigger) };
})()"""

_SEARCH_INPUT_FOCUSED_JS = """(() => {
  const input = document.querySelector('[data-search-input]');
  if (!(input instanceof HTMLInputElement)) return { ready: false, reason: 'no-search-input' };
  if (document.activeElement !== input) input.focus();
  return { ready: document.activeElement === input };
})()"""

# An instant whose calendar day differs between UTC and the browser's zone: late evening UTC lands on the next day east
# of Greenwich, early morning UTC on the previous day west of it. A client that reads the stored UTC wall clock as local
# time shows the UTC day and clock instead (``utcDay``, ``utcWallClock``).
_CROSS_DAY_INSTANT_JS = """(() => {
  const dayOptions = __DAY_OPTIONS__;
  const clockOptions = __CLOCK_OPTIONS__;
  for (const instant of ['2026-03-15T23:30:00Z', '2026-03-15T00:30:00Z']) {
    const moment = new Date(instant);
    if (moment.getDate() !== moment.getUTCDate()) {
      const utcWallClock = new Date(
        moment.getUTCFullYear(), moment.getUTCMonth(), moment.getUTCDate(), moment.getUTCHours(), moment.getUTCMinutes(),
      );
      return {
        instant,
        localDay: moment.toLocaleDateString(undefined, dayOptions),
        utcDay: moment.toLocaleDateString(undefined, { ...dayOptions, timeZone: 'UTC' }),
        localClock: moment.toLocaleString(undefined, clockOptions),
        utcWallClock: utcWallClock.toLocaleString(undefined, clockOptions),
      };
    }
  }
  return { instant: null };
})()""".replace("__DAY_OPTIONS__", _DAY_OPTIONS).replace("__CLOCK_OPTIONS__", _CLOCK_OPTIONS)

# Built with the local-time constructors the preset itself uses, so the expectation holds in any browser zone.
_MONTH_END_MARKS_JS = """(() => {
  const options = __DAY_OPTIONS__;
  const firstNoon = new Date(2026, 2, 1, 12, 0, 0, 0);
  return {
    sentAt: firstNoon.toISOString(),
    sentDay: firstNoon.toLocaleDateString(undefined, options),
    expectedSince: new Date(2026, 1, 28, 15, 0, 0, 0).toISOString(),
    expectedUntil: new Date(2026, 2, 31, 15, 0, 0, 0).toISOString(),
  };
})()""".replace("__DAY_OPTIONS__", _DAY_OPTIONS)

_CARD_DATES_JS = """(() => {
  const cards = new Set(Array.from(document.querySelectorAll('[data-cite-to-composer]')).map((cite) => cite.closest('div.relative')));
  const dates = Array.from(cards).map((card) => (card?.querySelector('svg[class*="lucide-calendar"]')?.parentElement?.textContent ?? '').trim());
  return { ready: dates.length > 0, dates };
})()"""

# The time filter button is the only titled button of the input row; the preset bar is the row's next sibling and lists
# today, week, month, quarter in that order.
_OPEN_TIME_FILTER_JS = """(() => {
  const toggle = document.querySelector('[data-search-input]')?.parentElement?.querySelector('button[title]');
  if (!(toggle instanceof HTMLElement)) return { ok: false, reason: 'no-time-filter-toggle' };
  toggle.click();
  return { ok: true };
})()"""

_PRESET_BAR_READY_JS = """(() => {
  const bar = document.querySelector('[data-search-input]')?.parentElement?.nextElementSibling;
  return { ready: (bar?.querySelectorAll('button').length ?? 0) >= 4 };
})()"""

# The preset reads the clock when it is chosen, so the clock is pinned for that click only.
_PIN_CLOCK_AND_CHOOSE_MONTH_JS = """(() => {
  const presets = Array.from(document.querySelector('[data-search-input]')?.parentElement?.nextElementSibling?.querySelectorAll('button') ?? []);
  if (presets.length < 4) return { ok: false, reason: 'no-presets', count: presets.length };
  const RealDate = window.Date;
  const pinned = new RealDate(2026, 2, 31, 15, 0, 0, 0).getTime();
  window.__realDate = RealDate;
  window.Date = class extends RealDate {
    constructor(...args) {
      if (args.length === 0) super(pinned);
      else super(...args);
    }
    static now() {
      return pinned;
    }
  };
  presets[2].click();
  return { ok: true };
})()"""

_MONTH_PRESET_ACTIVE_JS = """(() => {
  const presets = Array.from(document.querySelector('[data-search-input]')?.parentElement?.nextElementSibling?.querySelectorAll('button') ?? []);
  return { ready: presets[2]?.classList.contains('bg-primary') === true };
})()"""

_RESTORE_CLOCK_JS = """(() => {
  if (window.__realDate) {
    window.Date = window.__realDate;
    delete window.__realDate;
  }
  return { ok: true };
})()"""

_SEARCH_SETTLED_JS = """(() => {
  const requests = performance.getEntriesByType('resource')
    .filter((entry) => entry.name.includes('/chats/search'))
    .map((entry) => {
      const url = new URL(entry.name);
      return { since: url.searchParams.get('since'), until: url.searchParams.get('until'), done: entry.responseEnd > 0 };
    });
  const panel = document.querySelector('[data-search-input]')?.closest('div.rounded-xl');
  const loading = Boolean(panel?.querySelector('.animate-spin'));
  return { ready: requests.some((request) => request.since && request.done) && !loading, requests };
})()"""

# The shared-context panel's create row only exists once the Team Assets tab has mounted the panel.
_PANEL_READY_JS = """(() => ({ ready: !!document.querySelector('div[class*="grid-cols-[1fr_1.3fr_auto]"]') }))()"""

_PANEL_HISTORY_READY_JS = """(() => ({ ready: !!document.querySelector('div[class*="grid-cols-[1fr_150px_auto]"] input') }))()"""

# Selecting a context is what mounts its history section; the click happens once the context is listed.
_SELECT_CONTEXT_JS = """(() => {
  const target = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes(__NAME__));
  if (!(target instanceof HTMLElement)) return { ready: false };
  target.click();
  return { ready: true };
})()"""

# The search button stays disabled until the controlled input holds text; the click happens once it is enabled.
_SEARCH_HISTORY_JS = """(() => {
  const button = document.querySelector('div[class*="grid-cols-[1fr_150px_auto]"] button');
  if (!(button instanceof HTMLButtonElement) || button.disabled) return { ready: false };
  button.click();
  return { ready: true };
})()"""

# Hits are the children of the row's next sibling; the time is the header's last span.
_PANEL_HIT_TIMES_JS = """(() => {
  const hits = Array.from(document.querySelector('div[class*="grid-cols-[1fr_150px_auto]"]')?.nextElementSibling?.children ?? []);
  const times = hits.map((hit) => (hit.querySelector('div > span:last-child')?.textContent ?? '').trim());
  return { ready: times.length > 0, times };
})()"""


def _evaluate_dict(client: ChromeMcpClient, page: McpPage, expression: str) -> dict[str, object]:
    result = client.evaluate(page, expression, timeout_sec=15.0)
    assert isinstance(result, dict), result
    return result


def _type_react_controlled_text(client: ChromeMcpClient, page: McpPage, selector: str, text: str) -> None:
    """Fill a React-controlled input through the native setter plus an input event (CDP typing is unreliable)."""
    expression = f"""(() => {{
  const el = document.querySelector({json.dumps(selector)});
  if (!el) return {{ ok: false, err: 'input-not-found' }};
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  if (!setter) return {{ ok: false, err: 'setter-not-found' }};
  setter.call(el, {json.dumps(text)});
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  return {{ ok: true }};
}})()"""
    assert _evaluate_dict(client, page, expression).get("ok") is True


def _cross_day_instant(client: ChromeMcpClient, page: McpPage) -> dict[str, str]:
    probe = _evaluate_dict(client, page, _CROSS_DAY_INSTANT_JS)
    if not isinstance(probe.get("instant"), str):
        pytest.skip("the browser time zone keeps UTC and local calendar days aligned for both probed instants")
    return {key: str(value) for key, value in probe.items()}


def _strings(state: dict[str, object], key: str) -> set[str]:
    values = state[key]
    assert isinstance(values, list), state
    return {str(value) for value in values}


def _new_marker() -> str:
    return f"e2e{uuid.uuid4().hex[:12]}"


def _seed_prior_chat(api_url: str, sent_at: str, marker: str) -> None:
    http_json("POST", f"{api_url}/api/v1/chats/test/seed-prior-chat-fixture?sent_at={quote(sent_at, safe='')}&marker={marker}")


def _open_search_dialog(client: ChromeMcpClient, page: McpPage) -> None:
    client.evaluate(page, _OPEN_SEARCH_DIALOG_JS, timeout_sec=15.0)
    focus_state = wait_for_state(client, page, _SEARCH_INPUT_FOCUSED_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))
    assert focus_state.get("ready") is True, focus_state


def _api_search_sent_at_values(api_url: str, marker: str) -> list[str]:
    body = http_json("GET", f"{api_url}/api/v1/chats/search?q={marker}&limit=100")
    assert isinstance(body, dict)
    data = body["data"]
    assert isinstance(data, dict)
    items = data["items"]
    assert isinstance(items, list)
    return [str(item["sent_at"]) for item in items]


def _is_instant(value: str, expected: datetime) -> bool:
    """Whether ``value`` names ``expected``; a value without an offset is read as UTC, which is how it is stored."""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=parsed.tzinfo or UTC) == expected


def _assert_sent_at_carries_utc_offset(api_url: str, marker: str, instant: str) -> None:
    """The client gets the seeded instant with an explicit UTC offset, not a bare local-looking timestamp."""
    seeded = datetime.fromisoformat(instant.replace("Z", "+00:00"))
    values = _api_search_sent_at_values(api_url, marker)
    assert values, "the seeded messages are missing from the search API response"
    assert all(_is_instant(value, seeded) for value in values), values
    assert all(value.endswith("+00:00") for value in values), values


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="READ", workload="STANDARD")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_cmdk_result_date_follows_viewer_calendar_day_chrome_e2e() -> None:
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    warm_ui_route("/")
    marker = _new_marker()

    with open_mcp_page(f"{ui_url}/") as (client, page):
        wait_for_state(client, page, _CHAT_HOME_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(90.0))

        probe = _cross_day_instant(client, page)
        _seed_prior_chat(api_url, probe["instant"], marker)
        _assert_sent_at_carries_utc_offset(api_url, marker, probe["instant"])

        _open_search_dialog(client, page)
        _type_react_controlled_text(client, page, _SEARCH_INPUT, marker)
        shown = wait_for_state(client, page, _CARD_DATES_JS, timeout_sec=_warm_ui_parallel_wait_sec(60.0))
        assert _strings(shown, "dates") == {probe["localDay"]}, {"shown": shown["dates"], "utc_day": probe["utcDay"]}


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="READ", workload="STANDARD")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_cmdk_month_preset_reaches_the_first_of_the_month_chrome_e2e() -> None:
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    warm_ui_route("/")
    marker = _new_marker()

    with open_mcp_page(f"{ui_url}/") as (client, page):
        wait_for_state(client, page, _CHAT_HOME_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(90.0))

        marks = _evaluate_dict(client, page, _MONTH_END_MARKS_JS)
        _seed_prior_chat(api_url, str(marks["sentAt"]), marker)

        _open_search_dialog(client, page)
        assert _evaluate_dict(client, page, _OPEN_TIME_FILTER_JS).get("ok") is True
        wait_for_state(client, page, _PRESET_BAR_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))
        assert _evaluate_dict(client, page, _PIN_CLOCK_AND_CHOOSE_MONTH_JS).get("ok") is True
        wait_for_state(client, page, _MONTH_PRESET_ACTIVE_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))
        _evaluate_dict(client, page, _RESTORE_CLOCK_JS)

        _type_react_controlled_text(client, page, _SEARCH_INPUT, marker)
        settled = wait_for_state(client, page, _SEARCH_SETTLED_JS, timeout_sec=_warm_ui_parallel_wait_sec(60.0))
        requests = settled["requests"]
        assert isinstance(requests, list)
        windows = {(request["since"], request["until"]) for request in requests if request["since"]}
        assert windows == {(marks["expectedSince"], marks["expectedUntil"])}, windows

        # Only the 1st sits inside Feb 28 .. Mar 31 and outside Mar 3 .. Mar 31, so its cards tell the two windows apart.
        shown = wait_for_state(client, page, _CARD_DATES_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))
        assert _strings(shown, "dates") == {str(marks["sentDay"])}, shown["dates"]


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_shared_context_history_time_follows_viewer_clock_chrome_e2e() -> None:
    api_url = get_e2e_api_url()
    prepare_e2e_ui_session(api_url)
    marker = _new_marker()
    context_name = f"E2E history {marker}"
    created = http_json(
        "POST",
        f"{api_url}/api/v1/memory/shared-contexts",
        {"name": context_name, "description": "Search-hit time check", "policy": {"mode": "read_write"}},
        expected_statuses=frozenset({200, 201}),
    )
    assert isinstance(created, dict)
    context_id = str(created["id"])

    try:
        warm_ui_route("/settings/memory")
        with open_settings_subroute("/settings/memory?sub=team-hub", timeout_ms=120_000) as (client, page):
            dismiss_blocking_modals(client, page)
            wait_for_state(client, page, _PANEL_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(90.0))

            probe = _cross_day_instant(client, page)
            _seed_prior_chat(api_url, probe["instant"], marker)
            _assert_sent_at_carries_utc_offset(api_url, marker, probe["instant"])

            select_context = _SELECT_CONTEXT_JS.replace("__NAME__", json.dumps(context_name))
            wait_for_state(client, page, select_context, timeout_sec=_warm_ui_parallel_wait_sec(60.0))
            wait_for_state(client, page, _PANEL_HISTORY_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))
            _type_react_controlled_text(client, page, f"{_PANEL_HISTORY_ROW} input", marker)
            wait_for_state(client, page, _SEARCH_HISTORY_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))
            shown = wait_for_state(client, page, _PANEL_HIT_TIMES_JS, timeout_sec=_warm_ui_parallel_wait_sec(60.0))
            assert _strings(shown, "times") == {probe["localClock"]}, {
                "shown": shown["times"],
                "utc_wall_clock": probe["utcWallClock"],
            }
    finally:
        http_json("DELETE", f"{api_url}/api/v1/memory/shared-contexts/{context_id}")
