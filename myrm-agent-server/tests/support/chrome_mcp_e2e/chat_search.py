"""Cmd+K history search and the shared-context history panel: drive them and read the dates and times they show.

Each expectation is rendered by the browser that renders the surface, with the same ``toLocale*`` options as
``SearchResultCard.formatDate`` and ``formatSharedContextDate``, so it holds in any zone and any locale. Messages come
from the prior-chat fixture, which pins their send time and tags them with a one-off marker, so a check finds its own
messages and nobody else's.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TypedDict
from urllib.parse import quote

from chrome_mcp.client import ChromeMcpClient, McpPage

from .api import http_json
from .parallel_load import _warm_ui_parallel_wait_sec
from .wait_state import wait_for_state

SEARCH_INPUT = "[data-search-input]"

# The preset bar lists these in this order.
PRESET_INDEX = {"today": 0, "week": 1, "month": 2, "quarter": 3}

# Year, zero-based month, day and hour of a local time: the order of the JavaScript Date constructor.
LocalTime = tuple[int, int, int, int]

_DAY_OPTIONS = json.dumps({"month": "short", "day": "numeric", "year": "numeric"})
_CLOCK_OPTIONS = json.dumps({"month": "short", "day": "numeric", "hour": "2-digit", "minute": "2-digit"})

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

# An instant whose calendar day differs between UTC and the page's zone: late evening UTC lands on the next day east of
# Greenwich, early morning UTC on the previous day west of it. A client that reads the stored UTC wall clock as local time
# shows the UTC day and clock instead (``utcDay``, ``utcWallClock``).
_CROSS_DAY_INSTANT_JS = """(() => {
  const dayOptions = __DAY_OPTIONS__;
  const clockOptions = __CLOCK_OPTIONS__;
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  for (const instant of ['2026-03-15T23:30:00Z', '2026-03-15T00:30:00Z']) {
    const moment = new Date(instant);
    if (moment.getDate() !== moment.getUTCDate()) {
      const utcWallClock = new Date(
        moment.getUTCFullYear(), moment.getUTCMonth(), moment.getUTCDate(), moment.getUTCHours(), moment.getUTCMinutes(),
      );
      return {
        timeZone,
        instant,
        localDay: moment.toLocaleDateString(undefined, dayOptions),
        utcDay: moment.toLocaleDateString(undefined, { ...dayOptions, timeZone: 'UTC' }),
        localClock: moment.toLocaleString(undefined, clockOptions),
        utcWallClock: utcWallClock.toLocaleString(undefined, clockOptions),
      };
    }
  }
  return { timeZone, instant: null };
})()""".replace("__DAY_OPTIONS__", _DAY_OPTIONS).replace("__CLOCK_OPTIONS__", _CLOCK_OPTIONS)

# Built with the local-time constructor the presets use, so each expectation holds in any browser zone.
_LOCAL_MOMENTS_JS = """(() => {
  const options = __DAY_OPTIONS__;
  const moments = __TIMES__.map((time) => {
    const moment = new Date(...time);
    return { iso: moment.toISOString(), day: moment.toLocaleDateString(undefined, options) };
  });
  return { moments };
})()""".replace("__DAY_OPTIONS__", _DAY_OPTIONS)

# Cards are the unique wrappers of the cite buttons; a card's date sits beside its calendar icon.
_CARD_DATES_JS = """(() => {
  const cards = new Set(Array.from(document.querySelectorAll('[data-cite-to-composer]')).map((cite) => cite.closest('div.relative')));
  const dates = Array.from(cards).map((card) => (card?.querySelector('svg[class*="lucide-calendar"]')?.parentElement?.textContent ?? '').trim());
  return { ready: dates.length > 0, dates };
})()"""

# The time filter button is the only titled button of the input row; the preset bar is the row's next sibling.
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

# A preset reads the clock when it is chosen, so the clock is pinned from the click until the preset shows as active.
# Resource timings are cleared first: the next settled search is then the one this click caused.
_PIN_CLOCK_AND_CLICK_JS = """(() => {
  const presets = Array.from(document.querySelector('[data-search-input]')?.parentElement?.nextElementSibling?.querySelectorAll('button') ?? []);
  const target = presets[__PRESET__];
  if (presets.length < 4 || !target) return { ok: false, reason: 'no-preset', count: presets.length };
  const wasActive = target.classList.contains('bg-primary');
  const RealDate = window.Date;
  const pinned = new RealDate(...__CLOCK__).getTime();
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
  performance.clearResourceTimings();
  target.click();
  return { ok: true, wasActive };
})()"""

_PRESET_ACTIVE_JS = """(() => {
  const presets = Array.from(document.querySelector('[data-search-input]')?.parentElement?.nextElementSibling?.querySelectorAll('button') ?? []);
  return { ready: presets[__PRESET__]?.classList.contains('bg-primary') === __ACTIVE__ };
})()"""

_RESTORE_CLOCK_JS = """(() => {
  if (window.__realDate) {
    window.Date = window.__realDate;
    delete window.__realDate;
  }
  return { ok: true };
})()"""

# The last search the page sent since the timings were cleared, and the cards it left once it has finished.
_SETTLED_SEARCH_JS = """(() => {
  const requests = performance.getEntriesByType('resource')
    .filter((entry) => entry.name.includes('/chats/search'))
    .map((entry) => {
      const url = new URL(entry.name);
      return { since: url.searchParams.get('since'), until: url.searchParams.get('until'), done: entry.responseEnd > 0 };
    });
  const last = requests[requests.length - 1];
  const panel = document.querySelector('[data-search-input]')?.closest('div.rounded-xl');
  const loading = Boolean(panel?.querySelector('.animate-spin'));
  const cards = new Set(Array.from(document.querySelectorAll('[data-cite-to-composer]')).map((cite) => cite.closest('div.relative')));
  const dates = Array.from(cards).map((card) => (card?.querySelector('svg[class*="lucide-calendar"]')?.parentElement?.textContent ?? '').trim());
  return { ready: Boolean(last?.done) && !loading, since: last?.since ?? null, until: last?.until ?? null, dates };
})()"""

# The panel's create row only exists once the Team Assets tab has mounted the panel.
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


class LocalMoment(TypedDict):
    """A local time as the search API reads it (``iso``) and as the cards print its day (``day``)."""

    iso: str
    day: str


class SettledSearch(TypedDict):
    """What the last Cmd+K search asked for (``since``, ``until``; ``None`` when absent) and the day each card shows."""

    since: str | None
    until: str | None
    dates: list[str]


def new_marker() -> str:
    return f"e2e{uuid.uuid4().hex[:12]}"


def seed_prior_chat(api_url: str, sent_at: str, marker: str) -> None:
    http_json("POST", f"{api_url}/api/v1/chats/test/seed-prior-chat-fixture?sent_at={quote(sent_at, safe='')}&marker={marker}")


def evaluate_dict(client: ChromeMcpClient, page: McpPage, expression: str) -> dict[str, object]:
    result = client.evaluate(page, expression, timeout_sec=15.0)
    assert isinstance(result, dict), result
    return result


def _strings(state: dict[str, object], key: str) -> list[str]:
    values = state[key]
    assert isinstance(values, list), state
    return [str(value) for value in values]


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
    assert evaluate_dict(client, page, expression).get("ok") is True


def wait_for_chat_home(client: ChromeMcpClient, page: McpPage) -> None:
    wait_for_state(client, page, _CHAT_HOME_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(90.0))


def cross_day_instant(client: ChromeMcpClient, page: McpPage, time_zone: str) -> dict[str, str]:
    """An instant that falls on a different calendar day in UTC than in ``time_zone``, with how each reading renders it."""
    probe = evaluate_dict(client, page, _CROSS_DAY_INSTANT_JS)
    assert probe["timeZone"] == time_zone, probe
    assert isinstance(probe.get("instant"), str), f"{time_zone} keeps both probed instants on their UTC day: {probe}"
    return {key: str(value) for key, value in probe.items()}


def local_moments(client: ChromeMcpClient, page: McpPage, *times: LocalTime) -> list[LocalMoment]:
    state = evaluate_dict(client, page, _LOCAL_MOMENTS_JS.replace("__TIMES__", json.dumps(times)))
    moments = state["moments"]
    assert isinstance(moments, list), state
    return [LocalMoment(iso=str(moment["iso"]), day=str(moment["day"])) for moment in moments]


def _is_instant(value: str, expected: datetime) -> bool:
    """Whether ``value`` names ``expected``; a value without an offset is read as UTC, which is how it is stored."""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=parsed.tzinfo or UTC) == expected


def assert_sent_at_carries_utc_offset(api_url: str, marker: str, instant: str) -> None:
    """The client gets the seeded instant with an explicit UTC offset, not a bare local-looking timestamp."""
    body = http_json("GET", f"{api_url}/api/v1/chats/search?q={marker}&limit=100")
    assert isinstance(body, dict)
    data = body["data"]
    assert isinstance(data, dict)
    items = data["items"]
    assert isinstance(items, list)
    values = [str(item["sent_at"]) for item in items]
    seeded = datetime.fromisoformat(instant.replace("Z", "+00:00"))
    assert values, "the seeded messages are missing from the search API response"
    assert all(_is_instant(value, seeded) for value in values), values
    assert all(value.endswith("+00:00") for value in values), values


def open_search_dialog(client: ChromeMcpClient, page: McpPage) -> None:
    client.evaluate(page, _OPEN_SEARCH_DIALOG_JS, timeout_sec=15.0)
    focus_state = wait_for_state(client, page, _SEARCH_INPUT_FOCUSED_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))
    assert focus_state.get("ready") is True, focus_state


def search_in_dialog(client: ChromeMcpClient, page: McpPage, text: str) -> None:
    _type_react_controlled_text(client, page, SEARCH_INPUT, text)


def wait_for_card_dates(client: ChromeMcpClient, page: McpPage) -> list[str]:
    shown = wait_for_state(client, page, _CARD_DATES_JS, timeout_sec=_warm_ui_parallel_wait_sec(60.0))
    return _strings(shown, "dates")


def open_time_filter(client: ChromeMcpClient, page: McpPage) -> None:
    assert evaluate_dict(client, page, _OPEN_TIME_FILTER_JS).get("ok") is True
    wait_for_state(client, page, _PRESET_BAR_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))


def choose_preset(client: ChromeMcpClient, page: McpPage, preset: str, clock: LocalTime) -> None:
    """Click a preset with the page's clock pinned to ``clock``; clicking the active preset lifts the filter."""
    index = PRESET_INDEX[preset]
    clicked = evaluate_dict(
        client, page, _PIN_CLOCK_AND_CLICK_JS.replace("__PRESET__", str(index)).replace("__CLOCK__", json.dumps(clock))
    )
    try:
        assert clicked.get("ok") is True, clicked
        active = "false" if clicked["wasActive"] else "true"
        wait_for_state(
            client,
            page,
            _PRESET_ACTIVE_JS.replace("__PRESET__", str(index)).replace("__ACTIVE__", active),
            timeout_sec=_warm_ui_parallel_wait_sec(30.0),
        )
    finally:
        evaluate_dict(client, page, _RESTORE_CLOCK_JS)


def wait_for_settled_search(client: ChromeMcpClient, page: McpPage) -> SettledSearch:
    state = wait_for_state(client, page, _SETTLED_SEARCH_JS, timeout_sec=_warm_ui_parallel_wait_sec(60.0))
    since, until = state["since"], state["until"]
    assert since is None or isinstance(since, str), state
    assert until is None or isinstance(until, str), state
    return SettledSearch(since=since, until=until, dates=_strings(state, "dates"))


def wait_for_shared_context_panel(client: ChromeMcpClient, page: McpPage) -> None:
    wait_for_state(client, page, _PANEL_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(90.0))


def open_context_history(client: ChromeMcpClient, page: McpPage, context_name: str) -> None:
    wait_for_state(
        client,
        page,
        _SELECT_CONTEXT_JS.replace("__NAME__", json.dumps(context_name)),
        timeout_sec=_warm_ui_parallel_wait_sec(60.0),
    )
    wait_for_state(client, page, _PANEL_HISTORY_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))


def search_context_history(client: ChromeMcpClient, page: McpPage, text: str) -> list[str]:
    """The time shown on each hit of a history search in the selected shared context."""
    _type_react_controlled_text(client, page, f"{_PANEL_HISTORY_ROW} input", text)
    wait_for_state(client, page, _SEARCH_HISTORY_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))
    shown = wait_for_state(client, page, _PANEL_HIT_TIMES_JS, timeout_sec=_warm_ui_parallel_wait_sec(60.0))
    return _strings(shown, "times")
