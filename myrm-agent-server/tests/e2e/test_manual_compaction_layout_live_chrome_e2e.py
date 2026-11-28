"""Chrome E2E: what the strategy panel reports about a compression, on a phone and on a desktop screen, in both themes.

A user on a phone (a sheet that slides up) or at a desk (a popover) opens the panel behind the usage ring. Its focus field
and compress button must sit on the screen where a finger or a pointer can reach them, and every line the compression can
leave under the button must fit the panel and stay readable in the light and the dark theme: the tokens it saved and
"nothing left to compress" from the real model and server, and the two lines a user meets when the server pauses
summaries or a summary fails, which a healthy server cannot be made to produce and so are answered with the server's own
reply to a short chat, carrying that reason.
"""

from __future__ import annotations

import time
import uuid

import pytest
from cdp_chat.support import get_e2e_api_url, get_e2e_ui_url

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, http_json, wait_for_state
from tests.support.context_compaction_e2e import (
    chat_page,
    create_dormant_chat,
    ensure_locale,
    locale_text,
    prepare_live_model,
)
from tests.support.context_compaction_frame import (
    MIN_TEXT_CONTRAST,
    answer_compact_with_js,
    attach_chat_in_frame,
    close_app_frame,
    in_frame,
    open_app_frame,
    outcome_look_js,
    panel_layout_js,
)
from tests.support.context_compaction_ui import outcome_js, panel_ready_js, press_compress_js, rearm_compress_js

# Usage the last reply recorded; the panel offers "compress" from 30 %.
_RECORDED_USAGE_PERCENT = 55.0
_COMPACTION_TIMEOUT_SEC = 240.0
_DECLINE_TIMEOUT_SEC = 30.0
_TOKENS_PLACEHOLDER = "{tokens}"
_THEMES = ("light", "dark")
# A phone held upright, and a laptop screen: the app lays out for both from 1024px up and below it.
_LAYOUTS = [
    pytest.param((390, 844), "zh", id="phone-zh"),
    pytest.param((1280, 900), "en", id="desktop-en"),
]


def _ended_run(api_url: str, chat_id: str) -> dict[str, object]:
    """Ask the server to compact a short chat and return its whole ``GET /compact/run`` body once the run has ended.

    The server only accepts the request (202) and declines a chat this short within a moment.
    """
    http_json("POST", f"{api_url}/api/v1/chats/{chat_id}/compact", expected_statuses=frozenset({202}))
    deadline = time.monotonic() + _DECLINE_TIMEOUT_SEC
    while time.monotonic() < deadline:
        body = http_json("GET", f"{api_url}/api/v1/chats/{chat_id}/compact/run")
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(body, dict) and isinstance(data, dict) and data.get("status") != "running":
            return body
        time.sleep(0.2)
    raise AssertionError(f"the compaction of the short chat {chat_id} did not end within {_DECLINE_TIMEOUT_SEC:.0f} s")


def _open_panel(client: ChromeMcpClient, page: McpPage, chat_id: str, viewport: tuple[int, int], locale: str) -> None:
    """Load the chat in a fresh frame of ``viewport`` size and open the panel until its compress button is enabled."""
    open_app_frame(client, page, route=f"/{chat_id}", viewport=viewport)
    attach_chat_in_frame(client, page, chat_id)
    wait_for_state(client, page, in_frame(panel_ready_js(locale)), timeout_sec=90.0)


def _assert_panel_fits(client: ChromeMcpClient, page: McpPage, viewport: tuple[int, int], compress: str) -> dict[str, object]:
    """The open panel is on the screen, nothing scrolls sideways, and the focus field and the compress button can be pressed."""
    width, height = viewport
    panel = wait_for_state(client, page, panel_layout_js(compress), timeout_sec=30.0)
    assert panel["viewport"] == [width, height], panel
    assert panel["insideViewport"] is True, f"the panel is partly off the {width}x{height} screen: {panel}"
    assert panel["pageOverflowsX"] is False and panel["panelOverflowsX"] is False, f"the layout scrolls sideways: {panel}"
    for control in ("input", "button"):
        reached = panel[control]
        assert isinstance(reached, dict) and reached["inside"] is True and reached["reachable"] is True, (
            f"the {control} of the compress control cannot be reached at {width}x{height}: {panel}"
        )
    return panel


def _look_in_both_themes(client: ChromeMcpClient, page: McpPage, line: str) -> dict[str, dict[str, object]]:
    """How the line under the compress button looks in each theme: its contrast, its size and whether it fits the panel."""
    return {theme: wait_for_state(client, page, outcome_look_js(line, theme), timeout_sec=30.0) for theme in _THEMES}


def _unreadable(looks_by_line: dict[str, dict[str, dict[str, object]]]) -> list[str]:
    """Every line and theme whose text is below the contrast bar or does not fit the panel, so one run reports them all."""
    problems = []
    for line, looks in looks_by_line.items():
        for theme, look in looks.items():
            ratio = look["ratio"]
            if not (isinstance(ratio, float) and ratio >= MIN_TEXT_CONTRAST):
                problems.append(f"{line!r} reads at {ratio} : 1 in the {theme} theme (needs {MIN_TEXT_CONTRAST}): {look}")
            if look["clipped"] is not False or look["insideContainer"] is not True:
                problems.append(f"{line!r} does not fit the panel in the {theme} theme: {look}")
    return problems


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.parametrize(("viewport", "locale"), _LAYOUTS)
def test_compression_fits_the_panel_and_reads_in_both_themes_with_live_model(viewport: tuple[int, int], locale: str) -> None:
    """Compress from the panel, then press again: both results fit the screen and stay readable in light and dark."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()
    prepare_live_model(api_url)

    chat_id = f"e2elayoutcompact{uuid.uuid4().hex[:8]}"
    create_dormant_chat(api_url, chat_id, context_usage_percent=_RECORDED_USAGE_PERCENT)
    compress = locale_text(locale, "chat", "contextUsage", "strategy", "compressContext")
    saved_head, saved_tail = locale_text(locale, "chat", "contextUsage", "strategy", "compressSuccess").split(_TOKENS_PLACEHOLDER)
    assert saved_head.strip(), f"{locale}: the saved-tokens line opens with its value, so the line has no text to be found by"
    nothing = locale_text(locale, "chat", "compact", "skip", "nothing")

    with chat_page(ui_url) as (client, page, page_url):
        ensure_locale(client, page, locale, page_url=page_url)

        # The real compaction: the panel's controls first, then the line that reports the tokens saved.
        _open_panel(client, page, chat_id, viewport, locale)
        panel = _assert_panel_fits(client, page, viewport, compress)
        wait_for_state(client, page, in_frame(press_compress_js(locale)), timeout_sec=30.0)
        saved = wait_for_state(
            client, page, in_frame(outcome_js(head=saved_head, tail=saved_tail)), timeout_sec=_COMPACTION_TIMEOUT_SEC
        )
        looks = {saved_head: _look_in_both_themes(client, page, saved_head)}
        close_app_frame(client, page)

        # A page that loads the compacted chat afresh: nothing is left to compress.
        _open_panel(client, page, chat_id, viewport, locale)
        wait_for_state(client, page, in_frame(press_compress_js(locale)), timeout_sec=30.0)
        wait_for_state(client, page, in_frame(outcome_js(head=nothing, tail="")), timeout_sec=90.0)
        looks[nothing] = _look_in_both_themes(client, page, nothing)

    print(f"E2E_COMPACTION_LAYOUT chat={chat_id} viewport={viewport} locale={locale} panel={panel} saved={saved['shown']!r}")
    print(f"E2E_COMPACTION_LAYOUT looks={looks}")
    assert not (problems := _unreadable(looks)), problems


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.parametrize(("viewport", "locale"), _LAYOUTS)
def test_a_paused_or_failed_compaction_reads_in_both_themes(viewport: tuple[int, int], locale: str) -> None:
    """The server pausing summaries and a summary that failed: each line the panel shows fits the screen and stays readable."""
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url()

    # The server's own answer to a compression it declines, with the reason a paused or a failed summary carries.
    short_chat_id = f"e2elayoutdeclined{uuid.uuid4().hex[:8]}"
    create_dormant_chat(api_url, short_chat_id, rounds=1)
    declined = _ended_run(api_url, short_chat_id)
    data = declined["data"]
    assert isinstance(data, dict) and isinstance(data.get("result"), dict), declined
    result = data["result"]
    assert isinstance(result, dict) and result.get("compacted") is False, declined
    answers: dict[str, dict[str, object]] = {}
    for reason, key in (("summarize_circuit_open", ("compact", "skip", "paused")), ("timeout", ("compact", "failed"))):
        answers[locale_text(locale, "chat", *key)] = {**declined, "data": {**data, "result": {**result, "reason": reason}}}

    chat_id = f"e2elayoutpaused{uuid.uuid4().hex[:8]}"
    create_dormant_chat(api_url, chat_id, context_usage_percent=_RECORDED_USAGE_PERCENT)
    compress = locale_text(locale, "chat", "contextUsage", "strategy", "compressContext")

    with chat_page(ui_url) as (client, page, page_url):
        ensure_locale(client, page, locale, page_url=page_url)
        _open_panel(client, page, chat_id, viewport, locale)
        panel = _assert_panel_fits(client, page, viewport, compress)
        looks = {}
        for line, answer in answers.items():
            wait_for_state(client, page, answer_compact_with_js(answer), timeout_sec=15.0)
            wait_for_state(client, page, in_frame(rearm_compress_js()), timeout_sec=15.0)
            wait_for_state(client, page, in_frame(press_compress_js(locale)), timeout_sec=30.0)
            wait_for_state(client, page, in_frame(outcome_js(head=line, tail="")), timeout_sec=60.0)
            looks[line] = _look_in_both_themes(client, page, line)

    print(f"E2E_COMPACTION_LAYOUT chat={chat_id} viewport={viewport} locale={locale} panel={panel}")
    print(f"E2E_COMPACTION_LAYOUT looks={looks}")
    assert not (problems := _unreadable(looks)), problems
