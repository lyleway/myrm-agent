"""Chrome E2E (LIVE lane): an installed app asks the user's own model through the host.

Real business flow with the model the user configured; nothing on the path is mocked:

    the page declares `ai`; the user installs the app and allows it
    -> the page puts one question to the model; the host hands it to the server, which prices the largest call it could
       make, reserves that cost on the app's ledger, asks the configured model and settles what the call really cost
    -> a model with a known price answers the page and the activity log holds one `ai ok` entry that names the charged
       cost; a model without one is refused with `unavailable` before any call is made and the log says so
    -> a refused permission is answered with `permission_denied` and never reaches the model.

Which of the two answers the page gets is decided by the price table, not by the test: a gateway model the table does
not list is refused, and the charged branch only runs where the configured model has a price.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_installed_app_ai_live_chrome_e2e.py::<test_name>
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    dismiss_blocking_modals,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    open_mcp_page,
    prepare_e2e_ui_session,
    wait_e2e_provider_ready,
    wait_for_state,
    warm_ui_route,
)
from tests.support.e2e_provider_seed import seed_live_e2e_providers
from tests.support.e2e_runtime_guard import E2EResourceLedger
from tests.support.installed_app_e2e import (
    APPS_PATH,
    DISMISS_MIGRATION_JS,
    SEED_PATH,
    activity_counts,
    api_data,
    page_wait_sec,
    put_grant,
    run_app,
    seeded_app,
)

_HOST_READY_JS = "(() => ({ ready: Boolean(document.querySelector('[data-testid=\"app-host\"] iframe')) }))()"
# What the host writes for a model call that went through: the capability, the outcome and the cost it charged.
_CHARGED = re.compile(r"^\$\d+\.\d{4}$")


@contextmanager
def _installed_ai_app(api_url: str, ledger: E2EResourceLedger, *, ai_decision: str) -> Iterator[tuple[str, str]]:
    """The seeded page revised to declare `ai`, installed, with the user's decisions stored: `(artifact_id, app_url)`."""
    with seeded_app(api_url, ledger, install=False) as (chat_id, artifact_id):
        revised = http_json("POST", f"{api_url}{SEED_PATH}/revise", body={"chat_id": chat_id, "fetch_hosts": [], "ai": True})
        assert isinstance(revised, dict) and revised["artifact_id"] == artifact_id, revised
        api_data(http_json("PUT", f"{api_url}{APPS_PATH}/{artifact_id}"))
        put_grant(api_url, artifact_id, "notify", "allow", [])
        put_grant(api_url, artifact_id, "ai", ai_decision, [])
        app_url = f"{get_e2e_ui_url().rstrip('/')}/apps/{artifact_id}"
        warm_ui_route("/")
        warm_ui_route(f"/apps/{artifact_id}")
        yield artifact_id, app_url


@contextmanager
def _open_app_page(app_url: str) -> Iterator[tuple[ChromeMcpClient, McpPage]]:
    with open_mcp_page(app_url, timeout_ms=120_000) as (client, page):
        dismiss_blocking_modals(client, page)
        client.evaluate(page, DISMISS_MIGRATION_JS, timeout_sec=15.0)
        wait_for_state(client, page, _HOST_READY_JS, timeout_sec=page_wait_sec(), page_url=app_url)
        yield client, page


def _ready_live_model(api_url: str) -> None:
    prepare_e2e_ui_session(api_url)
    seed_live_e2e_providers(api_url)
    if not wait_e2e_provider_ready(timeout_sec=90.0):
        pytest.fail("Provider not ready — run ./myrm-dev ready --chrome")


def _ai_details(api_url: str, artifact_id: str, outcome: str) -> list[str | None]:
    """The detail of every `ai` activity entry with this outcome."""
    counts: Counter[tuple[str, str, str | None]] = activity_counts(api_url, artifact_id)
    return [detail for (capability, result, detail) in counts.elements() if (capability, result) == ("ai", outcome)]


def _wait_for_ai_activity(api_url: str, artifact_id: str, outcome: str) -> list[str | None]:
    """The host reports a refusal without waiting for the page's answer, so the log catches up a moment later."""
    deadline = time.monotonic() + 30.0
    details = _ai_details(api_url, artifact_id, outcome)
    while not details and time.monotonic() < deadline:
        time.sleep(1.0)
        details = _ai_details(api_url, artifact_id, outcome)
    return details


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_page_asks_the_users_model_and_the_log_matches_the_answer_live_chrome_e2e(
    e2e_resource_ledger: E2EResourceLedger,
) -> None:
    api_url = get_e2e_api_url()
    _ready_live_model(api_url)
    with _installed_ai_app(api_url, e2e_resource_ledger, ai_decision="allow") as (artifact_id, app_url):
        with _open_app_page(app_url) as (client, page):
            asked = run_app(client, page, 1, ask=True)
            assert asked["outcomes"] in (["ai:ok"], ["ai:unavailable"]), asked
            # The server writes the entry before it answers, so the log already agrees with what the page was told.
            if asked["outcomes"] == ["ai:ok"]:
                (charged,) = _ai_details(api_url, artifact_id, "ok")
                assert charged is not None and _CHARGED.match(charged), charged
                assert _ai_details(api_url, artifact_id, "error") == []
            else:
                assert _ai_details(api_url, artifact_id, "error") == ["unavailable"]
                assert _ai_details(api_url, artifact_id, "ok") == []


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_page_the_user_refused_never_reaches_the_model_live_chrome_e2e(e2e_resource_ledger: E2EResourceLedger) -> None:
    api_url = get_e2e_api_url()
    _ready_live_model(api_url)
    with _installed_ai_app(api_url, e2e_resource_ledger, ai_decision="deny") as (artifact_id, app_url):
        with _open_app_page(app_url) as (client, page):
            asked = run_app(client, page, 1, ask=True)
            assert asked["outcomes"] == ["ai:permission_denied"], asked
            # The refusal is reported by the page's host; the page must stay open until the report has arrived.
            assert _wait_for_ai_activity(api_url, artifact_id, "denied") == [None]
        assert _ai_details(api_url, artifact_id, "ok") == []
        assert _ai_details(api_url, artifact_id, "error") == []
