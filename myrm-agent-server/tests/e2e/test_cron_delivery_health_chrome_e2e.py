"""Chrome E2E: the delivery-health pill dates its verdict and says what the status rests on, over the real chain.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: the real Chrome session, the loopback API helpers and the settings page opener)
- POST /api/v1/cron/, POST /api/v1/cron/{id}/trigger, GET /api/v1/cron/{id}/delivery-health (POS: the real executor,
  run records and verdict route that the page reads)

[OUTPUT]
- A reminder delivered to an address that refuses the connection ends as a failed delivery, and the route answers with
  the time its newest attempt finished (`last_attempt_at`) and no `last_failed_at`
- The job's settings page shows the status word, the age of that attempt and, in the popover, the streak, the time of
  the last failure and the note that only real deliveries change the status

[POS]
Executor -> run record -> verdict route -> settings page, with nothing mocked. The reminder runner needs no model, so
the failed delivery comes without any external service.
"""

from __future__ import annotations

import os
import re
import socket
import time
from datetime import UTC, datetime, timedelta

import pytest

from tests.support.chrome_mcp_e2e import (
    dismiss_blocking_modals,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)

_VERDICT_TIMEOUT_SEC = 90.0
# The page reads the verdict once its job panel is open, and on a host under load that takes far longer than on an idle one.
_PILL_TIMEOUT_SEC = 120.0

# The words of the pill for each failing status, in both app locales.
_STATUS_WORDS = {
    "degraded": re.compile(r"Recent failures|近期有失败"),
    "down": re.compile(r"Cannot deliver|无法送达"),
}
# What Intl.RelativeTimeFormat says of an attempt that finished moments ago, in both app locales.
_AGE_WORDS = re.compile(r"\bnow\b|\bago\b|现在|前")
_BASIS_NOTE = re.compile(r"Status comes from real deliveries|状态依据真实投递得出")
_STREAK_LINE = re.compile(r"consecutive failure|连续失败")
_LAST_FAILURE_LINE = re.compile(r"Last failure|最近失败")

_PILL_JS = """(() => {
  const status = document.querySelector('[data-testid="connector-health-badge"]');
  const age = document.querySelector('[data-testid="connector-health-age"]');
  // What the page asked the server and how it went tells a pill that never came apart from one never asked for.
  const reads = performance
    .getEntriesByType('resource')
    .filter((entry) => entry.name.includes('/delivery-health'))
    .map((entry) => ({ status: entry.responseStatus, ms: Math.round(entry.duration) }));
  return {
    ready: status !== null && age !== null,
    status: status ? status.textContent.trim() : null,
    age: age ? age.textContent.trim() : null,
    reads,
    sample: (document.body.innerText || '').slice(0, 300),
  };
})()"""

_OPEN_POPOVER_JS = """(() => {
  const pill = document.querySelector('[data-testid="connector-health-badge"]')?.closest('button');
  if (!pill) {
    return { ready: false, reason: 'the pill of a failing destination is a button' };
  }
  if (pill.getAttribute('aria-expanded') !== 'true') {
    pill.click();
  }
  return { ready: true };
})()"""

_POPOVER_TEXT_JS = """(() => {
  const text = document.body.innerText || '';
  return { ready: /Status comes from real deliveries|状态依据真实投递得出/.test(text), text };
})()"""


def _closed_loopback_url() -> str:
    """A loopback address whose port is closed (bound ephemerally, then released), so a delivery to it fails at once."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}/hook"


def _create_reminder_to_closed_address(api_url: str) -> str:
    """A zero-model reminder that never fires on its own schedule within the test, delivering to a closed address."""
    namespace = os.environ.get("MYRM_E2E_NAMESPACE", "").strip() or "local"
    job = http_json(
        "POST",
        f"{api_url}/api/v1/cron/",
        {
            "name": f"e2e delivery health {namespace}",
            "job_type": "reminder",
            "prompt": "Delivery health probe",
            "schedule": {"kind": "cron", "expr": "0 0 1 1 *"},
            "delivery": {"channel": "webhook", "target": _closed_loopback_url()},
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


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(480)
def test_delivery_health_pill_dates_its_verdict_and_says_what_it_rests_on() -> None:
    api_url = get_e2e_api_url()
    job_id = _create_reminder_to_closed_address(api_url)
    try:
        verdict = _verdict_after_one_run(api_url, job_id)

        status = str(verdict["status"])
        assert status in _STATUS_WORDS, f"a refused delivery cannot leave the destination healthy: {verdict}"
        assert "last_failed_at" not in verdict, verdict
        attempt = datetime.fromisoformat(str(verdict["last_attempt_at"]).replace("Z", "+00:00"))
        assert attempt.tzinfo is not None, f"the time of the attempt must carry its zone: {verdict}"
        assert datetime.now(UTC) - timedelta(minutes=10) <= attempt <= datetime.now(UTC) + timedelta(minutes=2), verdict

        prepare_e2e_ui_session(api_url)
        warm_ui_route("/settings/cron")
        page_url = f"{get_e2e_ui_url().rstrip('/')}/settings/cron?job={job_id}"
        with open_settings_subroute(f"/settings/cron?job={job_id}", timeout_ms=90_000) as (client, page):
            dismiss_blocking_modals(client, page)

            pill = wait_for_state(client, page, _PILL_JS, timeout_sec=_PILL_TIMEOUT_SEC, page_url=page_url)
            assert pill.get("ready") is True, pill
            assert _STATUS_WORDS[status].fullmatch(str(pill["status"])), pill
            assert _AGE_WORDS.search(str(pill["age"])), pill

            opened = wait_for_state(client, page, _OPEN_POPOVER_JS, timeout_sec=30.0, page_url=page_url)
            assert opened.get("ready") is True, opened
            popover = wait_for_state(client, page, _POPOVER_TEXT_JS, timeout_sec=30.0, page_url=page_url)
            text = str(popover["text"])
            assert _BASIS_NOTE.search(text), popover
            assert _STREAK_LINE.search(text), popover
            assert _LAST_FAILURE_LINE.search(text), popover
    finally:
        # Removes the job together with its runs. A failure here chains onto a failing body, so neither is hidden
        # (pytest is configured to drop warnings, so a warning would not be seen).
        http_json("DELETE", f"{api_url}/api/v1/cron/{job_id}", expected_statuses=frozenset({204}))
