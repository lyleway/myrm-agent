"""Chrome E2E: what the user decides about an installed app stays decided, per capability, until the user changes it.

Each test is one flow on the seeded app page (it declares `notify` and `fetch: example.com`); the page's own calls go
through the product's host bridge and the server's proxy, nothing is mocked:

- the user allows one capability and refuses the other; the page gets exactly that, and a change made from the app
  menu applies to the very next call without reloading the page
- a burst of notifications is cut off after the host's limit, with one refusal logged for the whole burst and every
  concurrent activity write recorded, also when the page is closed right after the burst
- a new version of the page that asks for more hosts is asked again: what was approved before keeps working, the
  new request waits for the user, and the server never proxies beyond what the user approved
- the Artifacts page lists an installed app and opens it; an app that is not installed says so.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_installed_app_permissions_chrome_e2e.py::<test_name>
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

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
    wait_for_state,
    warm_ui_route,
)
from tests.support.e2e_runtime_guard import E2EResourceLedger
from tests.support.installed_app_e2e import (
    APPS_PATH,
    DIALOG_ACTIVITY_JS,
    DIALOG_STATE_JS,
    DISMISS_MIGRATION_JS,
    NO_DIALOG_JS,
    NOT_NOW,
    PERMISSIONS_ITEM,
    SAVE,
    SEED_PATH,
    activity_counts,
    api_data,
    app_detail,
    click_dialog_button,
    open_menu_item,
    page_wait_sec,
    put_grant,
    run_app,
    seeded_app,
    set_switches,
    wait_for_banner,
)

_EXAMPLE_COM, _EXAMPLE_ORG = "example.com", "example.org"
_APP_HOST_READY_JS = "(() => ({ ready: Boolean(document.querySelector('[data-testid=\"app-host\"] iframe')) }))()"
_APP_LIST_ENTRY_JS = """(async () => {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    const section = document.querySelector('section[aria-label="Apps"], section[aria-label="应用"]');
    const entry = section && Array.from(section.querySelectorAll('button')).find(
      (candidate) => (candidate.textContent || '').includes('pulse.html'),
    );
    if (entry) {
      entry.click();
      return { ok: true };
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return { ok: false, err: 'app-not-listed', body: document.body.innerText.slice(0, 300) };
})()"""
_NOT_INSTALLED_JS = """(() => ({
  ready: /This app isn't installed|这个应用尚未安装/.test(document.body.innerText),
  back: Array.from(document.querySelectorAll('button')).some((b) => /^(Back to artifacts|返回产物)$/.test((b.textContent || '').trim())),
}))()"""
_BACK_TO_ARTIFACTS_JS = """(() => {
  const back = Array.from(document.querySelectorAll('button')).find((b) => /^(Back to artifacts|返回产物)$/.test((b.textContent || '').trim()));
  if (!back) return { ok: false };
  back.click();
  return { ok: true };
})()"""
_PATH_IS_ARTIFACTS_JS = "(() => ({ ready: location.pathname === '/artifacts' }))()"


@contextmanager
def _open_app_page(app_url: str) -> Iterator[tuple[ChromeMcpClient, McpPage]]:
    with open_mcp_page(app_url, timeout_ms=120_000) as (client, page):
        dismiss_blocking_modals(client, page)
        client.evaluate(page, DISMISS_MIGRATION_JS, timeout_sec=15.0)
        yield client, page


def _grants(api_url: str, artifact_id: str) -> dict[str, tuple[str, list[str]]]:
    stored = app_detail(api_url, artifact_id)["grants"]
    assert isinstance(stored, list)
    return {str(grant["capability"]): (str(grant["decision"]), list(grant["scope"])) for grant in stored}


def _save(client: ChromeMcpClient, page: McpPage, app_url: str) -> None:
    click_dialog_button(client, page, SAVE)
    wait_for_state(client, page, NO_DIALOG_JS, timeout_sec=30.0, page_url=app_url)


def _ui() -> tuple[str, float]:
    return get_e2e_ui_url().rstrip("/"), page_wait_sec()


def _latest_sha(api_url: str, artifact_id: str) -> str:
    version = app_detail(api_url, artifact_id)["latest_version"]
    assert isinstance(version, dict), version
    return str(version["sha256_hash"])


def _wait_for_activity(api_url: str, artifact_id: str, expected: Counter[tuple[str, str, str | None]]) -> None:
    """The host reports page-side outcomes without waiting for the answer, so the log catches up a moment later."""
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if activity_counts(api_url, artifact_id) == expected:
            return
        time.sleep(1.0)
    assert activity_counts(api_url, artifact_id) == expected


def _outcomes(result: dict[str, object]) -> list[str]:
    outcomes = result["outcomes"]
    assert isinstance(outcomes, list), result
    return [str(outcome) for outcome in outcomes]


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_each_capability_is_decided_on_its_own_and_a_change_applies_without_reload(
    e2e_resource_ledger: E2EResourceLedger,
) -> None:
    api_url = get_e2e_api_url()
    ui_url, page_wait = _ui()
    prepare_e2e_ui_session(api_url)
    with seeded_app(api_url, e2e_resource_ledger, install=True) as (_chat_id, artifact_id):
        app_url = f"{ui_url}/apps/{artifact_id}"
        warm_ui_route("/")
        warm_ui_route(f"/apps/{artifact_id}")
        with _open_app_page(app_url) as (client, page):
            # The question opens by itself; the user keeps notifications and refuses network access.
            dialog = wait_for_state(client, page, DIALOG_STATE_JS, timeout_sec=page_wait, page_url=app_url)
            assert dialog["hosts"] == [_EXAMPLE_COM] and dialog["checked"] == ["true", "true"], dialog
            set_switches(client, page, [True, False])
            _save(client, page, app_url)
            assert _grants(api_url, artifact_id) == {"notify": ("allow", []), "fetch": ("deny", [_EXAMPLE_COM])}

            refused_fetch = run_app(client, page, 1)
            assert refused_fetch["outcomes"] == [
                "notify:ok",
                "fetch-declared:permission_denied",
                "fetch-undeclared:permission_denied",
            ], refused_fetch
            assert refused_fetch["toastSeen"] is True, refused_fetch

            # The standing page behind the app menu starts from what was decided and lists what the app did, by host only.
            open_menu_item(client, page, PERMISSIONS_ITEM)
            manage = wait_for_state(client, page, DIALOG_STATE_JS, timeout_sec=30.0, page_url=app_url)
            assert manage["checked"] == ["true", "false"], manage
            activity = wait_for_state(client, page, DIALOG_ACTIVITY_JS, timeout_sec=30.0, page_url=app_url)
            raw_lines = activity["lines"]
            assert isinstance(raw_lines, list), activity
            lines = [str(line) for line in raw_lines]
            assert (
                len(lines) == 3 and any(_EXAMPLE_COM in line for line in lines) and any(_EXAMPLE_ORG in line for line in lines)
            ), lines

            # Swap the two decisions: it applies to the next call, the page is not reloaded.
            set_switches(client, page, [False, True])
            _save(client, page, app_url)
            assert _grants(api_url, artifact_id) == {"notify": ("deny", []), "fetch": ("allow", [_EXAMPLE_COM])}
            swapped = run_app(client, page, 2)
            assert swapped["outcomes"] == [
                "notify:permission_denied",
                "fetch-declared:ok",
                "fetch-undeclared:host_not_granted",
            ], swapped

            _wait_for_activity(
                api_url,
                artifact_id,
                Counter(
                    {
                        ("notify", "ok", None): 1,
                        ("notify", "denied", None): 1,
                        ("fetch", "denied", _EXAMPLE_COM): 1,
                        ("fetch", "denied", _EXAMPLE_ORG): 2,
                        ("fetch", "ok", _EXAMPLE_COM): 1,
                    }
                ),
            )


# The host reports each shown notification and the first refusal of the window: six concurrent writes, all kept.
_BURST_ACTIVITY = Counter({("notify", "ok", None): 5, ("notify", "rate_limited", None): 1})


def _burst_of_notifications(api_url: str, ledger: E2EResourceLedger, *, stay_until_logged: bool) -> None:
    """Everything is decided, so no question is asked; seven notifications arrive in the same instant."""
    ui_url, page_wait = _ui()
    prepare_e2e_ui_session(api_url)
    with seeded_app(api_url, ledger, install=True) as (_chat_id, artifact_id):
        put_grant(api_url, artifact_id, "notify", "allow", [])
        put_grant(api_url, artifact_id, "fetch", "allow", [_EXAMPLE_COM])
        app_url = f"{ui_url}/apps/{artifact_id}"
        warm_ui_route("/")
        warm_ui_route(f"/apps/{artifact_id}")
        with _open_app_page(app_url) as (client, page):
            wait_for_state(client, page, _APP_HOST_READY_JS, timeout_sec=page_wait, page_url=app_url)
            burst = run_app(client, page, 1, burst=7)
            assert Counter(_outcomes(burst)) == Counter({"notify:ok": 5, "notify:rate_limited": 2}), burst
            if stay_until_logged:
                _wait_for_activity(api_url, artifact_id, _BURST_ACTIVITY)
        # The page is gone: whatever was still on its way to the server has to arrive anyway.
        _wait_for_activity(api_url, artifact_id, _BURST_ACTIVITY)


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_burst_of_notifications_is_cut_off_and_logged_once(e2e_resource_ledger: E2EResourceLedger) -> None:
    _burst_of_notifications(get_e2e_api_url(), e2e_resource_ledger, stay_until_logged=True)


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_closing_the_app_page_right_after_a_burst_keeps_its_log(e2e_resource_ledger: E2EResourceLedger) -> None:
    _burst_of_notifications(get_e2e_api_url(), e2e_resource_ledger, stay_until_logged=False)


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_new_version_that_asks_for_more_hosts_is_asked_again(e2e_resource_ledger: E2EResourceLedger) -> None:
    api_url = get_e2e_api_url()
    ui_url, page_wait = _ui()
    prepare_e2e_ui_session(api_url)
    with seeded_app(api_url, e2e_resource_ledger, install=True) as (chat_id, artifact_id):
        app_url = f"{ui_url}/apps/{artifact_id}"
        warm_ui_route("/")
        warm_ui_route(f"/apps/{artifact_id}")
        with _open_app_page(app_url) as (client, page):
            wait_for_state(client, page, DIALOG_STATE_JS, timeout_sec=page_wait, page_url=app_url)
            _save(client, page, app_url)
            approved = run_app(client, page, 1)
            assert approved["outcomes"] == ["notify:ok", "fetch-declared:ok", "fetch-undeclared:host_not_granted"], approved

            # An agent edits the page: the same app, a new version that also wants example.org.
            before = _latest_sha(api_url, artifact_id)
            revised = http_json(
                "POST", f"{api_url}{SEED_PATH}/revise", body={"chat_id": chat_id, "fetch_hosts": [_EXAMPLE_COM, _EXAMPLE_ORG]}
            )
            assert isinstance(revised, dict) and revised["artifact_id"] == artifact_id, revised
            after = _latest_sha(api_url, artifact_id)
            assert after != before

            # Whatever the page says, the server only proxies to what the user approved.
            body = {"url": f"https://{_EXAMPLE_ORG}/", "method": "GET", "headers": {}, "body": None, "version_sha256": after}
            refused = api_data(http_json("POST", f"{api_url}{APPS_PATH}/{artifact_id}/fetch", body=body))
            assert isinstance(refused, dict) and refused["ok"] is False and refused["code"] == "host_not_granted", refused

            # Opening the app again: the question comes back for network access, not for notifications.
            client.navigate(page, app_url, timeout_ms=90_000)
            dialog = wait_for_state(client, page, DIALOG_STATE_JS, timeout_sec=page_wait, page_url=app_url)
            assert dialog["hosts"] == [_EXAMPLE_COM, _EXAMPLE_ORG], dialog
            click_dialog_button(client, page, NOT_NOW)
            wait_for_state(client, page, NO_DIALOG_JS, timeout_sec=30.0, page_url=app_url)
            wait_for_banner(client, page, app_url, click=False)
            waiting = run_app(client, page, 2)
            assert waiting["outcomes"] == [
                "notify:ok",
                "fetch-declared:permission_denied",
                "fetch-undeclared:permission_denied",
            ], waiting
            assert _grants(api_url, artifact_id)["fetch"] == ("allow", [_EXAMPLE_COM])

            # Approved this time: both hosts answer.
            wait_for_banner(client, page, app_url, click=True)
            wait_for_state(client, page, DIALOG_STATE_JS, timeout_sec=30.0, page_url=app_url)
            _save(client, page, app_url)
            assert _grants(api_url, artifact_id)["fetch"] == ("allow", [_EXAMPLE_COM, _EXAMPLE_ORG])
            granted = run_app(client, page, 3)
            assert granted["outcomes"] == ["notify:ok", "fetch-declared:ok", "fetch-undeclared:ok"], granted


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_the_artifacts_page_lists_an_installed_app_and_opens_it(e2e_resource_ledger: E2EResourceLedger) -> None:
    api_url = get_e2e_api_url()
    ui_url, page_wait = _ui()
    prepare_e2e_ui_session(api_url)
    with seeded_app(api_url, e2e_resource_ledger, install=True) as (_chat_id, artifact_id):
        warm_ui_route("/artifacts")
        warm_ui_route(f"/apps/{artifact_id}")
        with _open_app_page(f"{ui_url}/artifacts") as (client, page):
            opened = client.evaluate(page, _APP_LIST_ENTRY_JS, timeout_sec=90.0)
            assert isinstance(opened, dict) and opened.get("ok") is True, opened
            wait_for_state(client, page, _APP_HOST_READY_JS, timeout_sec=page_wait, page_url=f"{ui_url}/apps/{artifact_id}")
            assert str(client.evaluate(page, "location.pathname", timeout_sec=15.0)) == f"/apps/{artifact_id}"


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="READ", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_an_app_that_is_not_installed_says_so_and_leads_back() -> None:
    api_url = get_e2e_api_url()
    ui_url, page_wait = _ui()
    prepare_e2e_ui_session(api_url)
    unknown = f"file_missing{uuid4().hex[:8]}"
    app_url = f"{ui_url}/apps/{unknown}"
    warm_ui_route(f"/apps/{unknown}")
    warm_ui_route("/artifacts")
    with _open_app_page(app_url) as (client, page):
        state = wait_for_state(client, page, _NOT_INSTALLED_JS, timeout_sec=page_wait, page_url=app_url)
        assert state["back"] is True, state
        left = client.evaluate(page, _BACK_TO_ARTIFACTS_JS, timeout_sec=15.0)
        assert isinstance(left, dict) and left.get("ok") is True, left
        wait_for_state(client, page, _PATH_IS_ARTIFACTS_JS, timeout_sec=60.0, page_url=f"{ui_url}/artifacts")
