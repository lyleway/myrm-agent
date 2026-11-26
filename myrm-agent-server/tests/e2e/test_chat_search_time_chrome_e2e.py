"""Chrome E2E: chat-history search shows message times on the viewer's clock, and the Cmd+K time presets step by calendar
months (no LLM).

The prior-chat seed stamps its messages at a pinned instant and tags them with a one-off marker that each check searches
for, and each check pins the browser's time zone, so a check holds or fails with the stack, not with the time of day or
the zone of the host that runs it:
- an instant that falls on a different UTC day than the viewer's day shows the viewer's day on the Cmd+K result card and
  the viewer's clock time on a shared-context history hit, east and west of Greenwich, and the search API hands the
  client that instant with an explicit UTC offset;
- at the end of a month the Cmd+K presets reach back by calendar (Mar 31 less a month is Feb 28, May 31 less a quarter is
  Feb 28), the page asks the search API for exactly that window, the history inside it is what the cards show, and
  choosing the active preset again lifts the filter.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pytest

from tests.support.chrome_mcp_e2e import (
    chat_search,
    dismiss_blocking_modals,
    emulated_time_zone,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    open_mcp_page,
    open_settings_subroute,
    prepare_e2e_ui_session,
    warm_ui_route,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

_EAST = "Asia/Shanghai"
_WEST = "America/Los_Angeles"

# The two seeded chats (local times): Feb 28 16:00 falls in a window that starts on Feb 28 at 15:00, Feb 27 12:00 does not.
_INSIDE_SEED: chat_search.LocalTime = (2026, 1, 28, 16)
_OUTSIDE_SEED: chat_search.LocalTime = (2026, 1, 27, 12)


@dataclass(frozen=True)
class _Step:
    """Choose ``preset``: the page then asks for the window from ``since`` (``None``: no window) up to the pinned clock."""

    preset: str
    since: chat_search.LocalTime | None
    shown: frozenset[str]


_NOTHING: frozenset[str] = frozenset()
_BOTH = frozenset({"inside", "outside"})
_INSIDE = frozenset({"inside"})

# Mar 31, 15:00 local: today starts at midnight, the week at Mar 24, the quarter reaches Dec 31 (no clamp), the month
# clamps to Feb 28 and the seed from Feb 28 stays in it.
_MARCH_END_STEPS = (
    _Step("today", (2026, 2, 31, 0), _NOTHING),
    _Step("week", (2026, 2, 24, 15), _NOTHING),
    _Step("quarter", (2025, 11, 31, 15), _BOTH),
    _Step("month", (2026, 1, 28, 15), _INSIDE),
    _Step("month", None, _BOTH),
)

# May 31, 15:00 local: the quarter clamps to Feb 28, the month to Apr 30.
_MAY_END_STEPS = (
    _Step("quarter", (2026, 1, 28, 15), _INSIDE),
    _Step("month", (2026, 3, 30, 15), _NOTHING),
    _Step("month", None, _BOTH),
)


def _check_cmdk_result_date(time_zone: str) -> None:
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    warm_ui_route("/")
    marker = chat_search.new_marker()

    with open_mcp_page(f"{ui_url}/") as (client, page):
        chat_search.wait_for_chat_home(client, page)
        with emulated_time_zone(client, page, time_zone):
            probe = chat_search.cross_day_instant(client, page, time_zone)
            chat_search.seed_prior_chat(api_url, probe["instant"], marker)
            chat_search.assert_sent_at_carries_utc_offset(api_url, marker, probe["instant"])

            chat_search.open_search_dialog(client, page)
            chat_search.search_in_dialog(client, page, marker)
            shown = chat_search.wait_for_card_dates(client, page)
            assert set(shown) == {probe["localDay"]}, {"shown": shown, "utc_day": probe["utcDay"]}


@contextmanager
def _shared_context(api_url: str, marker: str) -> Iterator[str]:
    name = f"E2E history {marker}"
    created = http_json(
        "POST",
        f"{api_url}/api/v1/memory/shared-contexts",
        {"name": name, "description": "Search-hit time check", "policy": {"mode": "read_write"}},
        expected_statuses=frozenset({200, 201}),
    )
    assert isinstance(created, dict)
    try:
        yield name
    finally:
        http_json("DELETE", f"{api_url}/api/v1/memory/shared-contexts/{created['id']}")


def _check_shared_context_hit_time(time_zone: str) -> None:
    api_url = get_e2e_api_url()
    prepare_e2e_ui_session(api_url)
    marker = chat_search.new_marker()

    with _shared_context(api_url, marker) as context_name:
        warm_ui_route("/settings/memory")
        with open_settings_subroute("/settings/memory?sub=team-hub", timeout_ms=120_000) as (client, page):
            dismiss_blocking_modals(client, page)
            chat_search.wait_for_shared_context_panel(client, page)
            with emulated_time_zone(client, page, time_zone):
                probe = chat_search.cross_day_instant(client, page, time_zone)
                chat_search.seed_prior_chat(api_url, probe["instant"], marker)
                chat_search.assert_sent_at_carries_utc_offset(api_url, marker, probe["instant"])

                chat_search.open_context_history(client, page, context_name)
                shown = chat_search.search_context_history(client, page, marker)
                assert set(shown) == {probe["localClock"]}, {"shown": shown, "utc_wall_clock": probe["utcWallClock"]}


def _check_presets(time_zone: str, clock: chat_search.LocalTime, steps: tuple[_Step, ...]) -> None:
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_e2e_ui_session(api_url)
    warm_ui_route("/")
    marker = chat_search.new_marker()

    with open_mcp_page(f"{ui_url}/") as (client, page):
        chat_search.wait_for_chat_home(client, page)
        with emulated_time_zone(client, page, time_zone):
            wanted = sorted({_INSIDE_SEED, _OUTSIDE_SEED, clock, *(step.since for step in steps if step.since)})
            moment = dict(zip(wanted, chat_search.local_moments(client, page, *wanted), strict=True))
            seeds = {"inside": moment[_INSIDE_SEED], "outside": moment[_OUTSIDE_SEED]}
            for seed in seeds.values():
                chat_search.seed_prior_chat(api_url, seed["iso"], marker)

            # Search first, then narrow it: the preset re-runs the search the dialog already holds.
            chat_search.open_search_dialog(client, page)
            chat_search.search_in_dialog(client, page, marker)
            unfiltered = chat_search.wait_for_card_dates(client, page)
            assert set(unfiltered) == {seed["day"] for seed in seeds.values()}, unfiltered

            chat_search.open_time_filter(client, page)
            for step in steps:
                chat_search.choose_preset(client, page, step.preset, clock)
                settled = chat_search.wait_for_settled_search(client, page)
                window = (moment[step.since]["iso"], moment[clock]["iso"]) if step.since else (None, None)
                assert (settled["since"], settled["until"]) == window, {"step": step, "settled": settled}
                assert set(settled["dates"]) == {seeds[name]["day"] for name in step.shown}, {"step": step, "settled": settled}


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="READ", workload="STANDARD")
@pytest.mark.e2e_search_policy("empty")
def test_cmdk_result_date_follows_viewer_calendar_day_east_of_utc_chrome_e2e() -> None:
    _check_cmdk_result_date(_EAST)


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="READ", workload="STANDARD")
@pytest.mark.e2e_search_policy("empty")
def test_cmdk_result_date_follows_viewer_calendar_day_west_of_utc_chrome_e2e() -> None:
    _check_cmdk_result_date(_WEST)


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="READ", workload="STANDARD")
@pytest.mark.e2e_search_policy("empty")
def test_cmdk_presets_at_the_end_of_march_west_of_utc_chrome_e2e() -> None:
    _check_presets(_WEST, (2026, 2, 31, 15), _MARCH_END_STEPS)


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="READ", workload="STANDARD")
@pytest.mark.e2e_search_policy("empty")
def test_cmdk_presets_at_the_end_of_may_east_of_utc_chrome_e2e() -> None:
    _check_presets(_EAST, (2026, 4, 31, 15), _MAY_END_STEPS)


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.e2e_search_policy("empty")
def test_shared_context_hit_time_follows_viewer_clock_east_of_utc_chrome_e2e() -> None:
    _check_shared_context_hit_time(_EAST)


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.e2e_search_policy("empty")
def test_shared_context_hit_time_follows_viewer_clock_west_of_utc_chrome_e2e() -> None:
    _check_shared_context_hit_time(_WEST)
