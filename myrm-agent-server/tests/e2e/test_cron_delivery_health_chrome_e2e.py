"""Chrome E2E: the delivery-health pill dates its verdict, says what the status rests on and stays legible, over the real chain.

A reminder that delivers to an address inside the network is refused by the application's SSRF guard before any request
goes out, which takes the destination down at once with the category ``address_rejected``. That is the failure a model-free
run can produce on the isolated stack; a destination that answers (healthy, degraded, recovery) needs a receiver the
guard lets through and is covered over real HTTP deliveries in tests/api/cron/test_delivery_health_http_chain.py.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: the real Chrome session, the loopback API helpers and the settings page opener)
- tests.support.cron_delivery_health_probes (POS: the page-side scripts that open the job, read the pill and measure it)
- POST /api/v1/cron/, POST /api/v1/cron/{id}/trigger, GET /api/v1/cron/{id}/delivery-health (POS: the real executor,
  run records and verdict route that the page reads)
- myrm-agent-frontend/locales/{en,zh}.json (POS: the copy the page must show, so the test follows it instead of restating it)

[OUTPUT]
- The route answers a refused local address with status ``down``, a streak of one, the category ``address_rejected`` and
  the time its newest attempt finished (`last_attempt_at`), without `last_failed_at`
- In both app languages the job's settings page shows the status word, an age that fits the attempt, and in the popover
  the category and its hint, the streak, the time of the last failure and the note that only real deliveries change the status
- Opened on a phone-width viewport the pill and its popover fit the screen, the page does not scroll sideways, and all
  of their text keeps a contrast of at least 4.5 in both themes

[POS]
Executor -> run record -> verdict route -> settings page, with nothing mocked. The reminder runner needs no model, so
the failed delivery comes without any external service.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import cast

import pytest

from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    _warm_ui_parallel_wait_sec,
    dismiss_blocking_modals,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    open_mcp_page,
    open_settings_subroute,
    prepare_e2e_ui_session,
    reload_mcp_page,
    wait_for_state,
    warm_ui_route,
)
from tests.support.cron_delivery_health_probes import (
    AGE,
    FRAME_MEASURE,
    FRAME_PILL,
    FRAME_PRELUDE,
    HOST_PRELUDE,
    OPEN_POPOVER,
    PILL,
    POPOVER_TEXT,
    probe,
)

_LOCALES_DIR = Path(__file__).resolve().parents[3] / "myrm-agent-frontend" / "locales"
_LOCALES = ("en", "zh")

# Inside the network, so the SSRF guard refuses it before anything is sent; the port is never reached.
_LOCAL_ADDRESS = "http://127.0.0.1:9/hook"
_VERDICT_TIMEOUT_SEC = 90.0
# The page reads the verdict once its job panel is open, and on a host under load that takes far longer than on an idle one.
_PILL_TIMEOUT_SEC = 120.0
_PHONE_VIEWPORT = (390, 844)
_MIN_TEXT_CONTRAST = 4.5


@cache
def _cron_messages(locale: str) -> dict[str, str]:
    messages: dict[str, str] = json.loads((_LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))["cron"]
    return messages


def _one_failure(locale: str) -> str:
    """The streak line for a single failure, read off the locale's own message (a plural in English)."""
    message = _cron_messages(locale)["connectorFailureStreak"]
    plural_one = re.search(r"one \{# ([^}]+)\}", message)
    return f"1 {plural_one.group(1)}" if plural_one else message.replace("{count}", "1")


def _create_reminder_to_local_address(api_url: str, name: str) -> str:
    """A zero-model reminder that never fires on its own schedule within the test, delivering to a local address."""
    job = http_json(
        "POST",
        f"{api_url}/api/v1/cron/",
        {
            "name": name,
            "job_type": "reminder",
            "prompt": "Delivery health probe",
            "schedule": {"kind": "cron", "expr": "0 0 1 1 *"},
            "delivery": {"channel": "webhook", "target": _LOCAL_ADDRESS},
        },
    )
    job_id = job.get("id") if isinstance(job, dict) else None
    assert isinstance(job_id, str), job
    return job_id


def _verdict_after_one_run(api_url: str, job_id: str) -> dict[str, object]:
    """Run the job once by hand and wait for the route to judge the destination (204 means nothing to judge yet)."""
    http_json("POST", f"{api_url}/api/v1/cron/{job_id}/trigger", expected_statuses=frozenset({202}))
    deadline = time.monotonic() + _VERDICT_TIMEOUT_SEC
    while time.monotonic() < deadline:
        verdict = http_json("GET", f"{api_url}/api/v1/cron/{job_id}/delivery-health")
        if isinstance(verdict, dict) and verdict.get("status"):
            return verdict
        time.sleep(1.0)
    raise AssertionError(f"no delivery verdict within {_VERDICT_TIMEOUT_SEC:.0f}s of the run of job {job_id}")


def _assert_refused_verdict(verdict: dict[str, object]) -> None:
    """What the route must say about a destination whose address was refused."""
    assert (verdict["status"], verdict["failure_streak"]) == ("down", 1), verdict
    assert verdict["last_error_category"] == "address_rejected", verdict
    assert "last_failed_at" not in verdict, verdict
    attempt = datetime.fromisoformat(str(verdict["last_attempt_at"]))
    assert attempt.tzinfo is not None, f"the time of the attempt must carry its zone: {verdict}"
    assert datetime.now(UTC) - timedelta(minutes=10) <= attempt <= datetime.now(UTC) + timedelta(minutes=2), verdict


def _stored_delivery(api_url: str, job_id: str) -> object:
    """The delivery the server holds for the job: the pill judges that destination, whatever the job was created with."""
    job = http_json("GET", f"{api_url}/api/v1/cron/{job_id}")
    return job.get("delivery") if isinstance(job, dict) else job


@contextmanager
def _refused_destination(api_url: str) -> Iterator[tuple[str, str, dict[str, object]]]:
    """A job whose delivery to a local address was refused once, as (job id, job name, the route's verdict); removed on exit.

    The name is unique, so the page's list can be told which card is the job's.
    """
    name = f"e2e delivery health {uuid.uuid4().hex[:8]}"
    job_id = _create_reminder_to_local_address(api_url, name)
    try:
        yield job_id, name, _verdict_after_one_run(api_url, job_id)
    finally:
        # Removes the job together with its runs. A failure here chains onto a failing body, so neither is hidden
        # (pytest is configured to drop warnings, so a warning would not be seen).
        http_json("DELETE", f"{api_url}/api/v1/cron/{job_id}", expected_statuses=frozenset({204}))


def _set_locale_cookie(client: ChromeMcpClient, page: McpPage, locale: str | None) -> None:
    """Set the language cookie as the picker does; ``None`` removes it."""
    cookie = f"NEXT_LOCALE={locale}; path=/; max-age=31536000" if locale else "NEXT_LOCALE=; path=/; max-age=0"
    client.evaluate(page, f"(() => {{ document.cookie = {json.dumps(cookie)}; return {{ ok: true }}; }})()", timeout_sec=15.0)


def _assert_page_says_what_the_locale_says(
    client: ChromeMcpClient, page: McpPage, locale: str, attempt_at: str, page_url: str, pill_probe: str
) -> None:
    messages = _cron_messages(locale)
    pill_timeout_sec = _warm_ui_parallel_wait_sec(_PILL_TIMEOUT_SEC)
    pill = wait_for_state(client, page, pill_probe, timeout_sec=pill_timeout_sec, page_url=page_url)
    if pill.get("lang") != locale:
        # The browser starts in the language its profile asks for. A reload through the harness waits for the load; a
        # bare ``location.reload()`` would leave the next probe racing the navigation.
        _set_locale_cookie(client, page, locale)
        reload_mcp_page(client, page, target_url=page_url, timeout_ms=90_000)
        pill = wait_for_state(client, page, pill_probe, timeout_sec=pill_timeout_sec, page_url=page_url)
    assert pill.get("ready") is True, pill
    assert pill["lang"] == locale, pill
    assert pill["status"] == messages["connectorStatusDown"], pill

    spec = {
        "locale": locale,
        "attemptAt": attempt_at,
        "headline": messages["connectorCategoryAddress"],
        "hint": messages["connectorCategoryAddressHint"],
        "streak": _one_failure(locale),
        "lastFailure": messages["connectorLastFailedAt"],
        "basis": messages["connectorHealthBasis"],
    }
    age = client.evaluate(page, probe(HOST_PRELUDE, AGE, spec=spec), timeout_sec=15.0)
    assert age.get("allowedAge") is True, f"the pill's age does not fit the attempt: {age}"

    opened = wait_for_state(client, page, probe(HOST_PRELUDE, OPEN_POPOVER), timeout_sec=30.0, page_url=page_url)
    assert opened.get("ready") is True, opened
    popover = wait_for_state(client, page, probe(HOST_PRELUDE, POPOVER_TEXT, spec=spec), timeout_sec=30.0, page_url=page_url)
    assert popover.get("missing") == [], popover


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_delivery_health_pill_dates_its_verdict_and_says_what_it_rests_on_in_both_languages() -> None:
    api_url = get_e2e_api_url()
    with _refused_destination(api_url) as (job_id, name, verdict):
        _assert_refused_verdict(verdict)
        attempt_at = str(verdict["last_attempt_at"])

        prepare_e2e_ui_session(api_url)
        warm_ui_route("/settings/cron")
        # The list, not the job's own page: the harness presses every button of a page that reads like a dismissal, and on
        # a job's panel that includes the delivery choice "Off" (关闭), which would re-point the job and leave no verdict.
        page_url = f"{get_e2e_ui_url().rstrip('/')}/settings/cron"
        pill_probe = probe(HOST_PRELUDE, PILL, name=name)
        with open_settings_subroute("/settings/cron", timeout_ms=90_000) as (client, page):
            try:
                for locale in _LOCALES:
                    _assert_page_says_what_the_locale_says(client, page, locale, attempt_at, page_url, pill_probe)
            except AssertionError as exc:
                raise AssertionError(f"{exc}\nthe job's stored delivery: {_stored_delivery(api_url, job_id)}") from exc
            finally:
                _set_locale_cookie(client, page, None)


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_delivery_health_pill_and_popover_fit_a_phone_and_stay_legible_in_both_themes() -> None:
    api_url = get_e2e_api_url()
    with _refused_destination(api_url) as (job_id, _, verdict):
        _assert_refused_verdict(verdict)

        prepare_e2e_ui_session(api_url)
        warm_ui_route("/settings/cron")
        with open_mcp_page(f"{get_e2e_ui_url().rstrip('/')}/", timeout_ms=90_000) as (client, page):
            dismiss_blocking_modals(client, page)
            width, height = _PHONE_VIEWPORT
            # The frame is a second full app load: on a cold dev server its chunks can take minutes to hydrate.
            frame_probe = probe(FRAME_PRELUDE, FRAME_PILL, width=width, height=height, job=job_id)
            wait_for_state(client, page, frame_probe, timeout_sec=_warm_ui_parallel_wait_sec(240.0))
            wait_for_state(client, page, probe(FRAME_PRELUDE, OPEN_POPOVER), timeout_sec=60.0)

            backgrounds: set[tuple[int, ...]] = set()
            for theme in ("dark", "light"):
                measured = wait_for_state(client, page, probe(FRAME_PRELUDE, FRAME_MEASURE, theme=theme), timeout_sec=60.0)
                # Spelled out: pytest abbreviates a dict in an assertion message, which hides where a box is.
                shown = json.dumps(measured, sort_keys=True)
                assert measured["viewportWidth"] == width, shown
                assert measured["popoverInside"] is True and measured["pillInside"] is True, shown
                assert measured["pageOverflowsX"] is False, shown
                backgrounds.add(tuple(cast(list[int], measured["popoverBackground"])))
                weak = {
                    name: ratio
                    for name, ratio in cast(dict[str, float], measured["contrasts"]).items()
                    if ratio < _MIN_TEXT_CONTRAST
                }
                assert not weak, f"text below {_MIN_TEXT_CONTRAST}:1 in {theme}: {weak}"
            # Both themes must have been measured: a class swap that changed nothing would pass the check twice.
            assert len(backgrounds) == 2, backgrounds
