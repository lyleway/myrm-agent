"""Chrome E2E: an HTML artifact installed as an app uses the bridge only for what the user allowed.

Real business flow, no mock; the artifact is registered by the product's own processor and the install, the permission
dialog, the proxied fetch and the activity log are the product's own:

    an answer carries an HTML app that declares `notify` and `fetch` for example.com
    -> the user presses "Install as app" on its card and lands on the app's page
    -> the permission dialog opens by itself; "Not now" leaves both capabilities off
    -> the app's calls are refused while nothing is decided, and the page shows the "permissions to review" banner
    -> the banner reopens the dialog; saving allows both
    -> the app's notification reaches the user, its call to the declared host is answered by the server's proxy and
       its call to a host outside the declaration is refused by the server
    -> the activity log holds each refusal and each success, with the host name only
    -> uninstalling from the app menu removes the app and its page.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_installed_app_chrome_e2e.py::<test_name>
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from tests.support.chrome_mcp_e2e import (
    dismiss_blocking_modals,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    http_json_once,
    open_mcp_page,
    prepare_e2e_ui_session,
    wait_e2e_provider_ready,
    wait_for_react_e2e_bridge,
    wait_for_state,
    warm_ui_route,
)
from tests.support.e2e_provider_seed import seed_live_e2e_providers
from tests.support.e2e_runtime_guard import E2EResourceLedger
from tests.support.installed_app_e2e import (
    APPS_PATH,
    ATTACH_CHAT_JS,
    CONFIRM_UNINSTALL_JS,
    DIALOG_STATE_JS,
    DISMISS_MIGRATION_JS,
    INSTALL_FROM_CARD_JS,
    NO_DIALOG_JS,
    NOT_NOW,
    SAVE,
    SEED_PATH,
    UNINSTALL_ITEM,
    activity_counts,
    app_detail,
    click_dialog_button,
    installed_ids,
    open_menu_item,
    page_wait_sec,
    run_app,
    wait_for_banner,
)


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_an_installed_app_uses_the_bridge_only_for_what_the_user_allowed_chrome_e2e(
    e2e_resource_ledger: E2EResourceLedger,
) -> None:
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url().rstrip("/")
    prepare_e2e_ui_session(api_url)
    # The chat page of a fresh runtime only opens once a model is configured, the way a user's first setup does.
    seed_live_e2e_providers(api_url)
    if not wait_e2e_provider_ready(timeout_sec=90.0):
        pytest.fail("Provider not ready — run ./myrm-dev ready --chrome")
    seeded = http_json("POST", f"{api_url}{SEED_PATH}")
    assert isinstance(seeded, dict), seeded
    chat_id, artifact_id = str(seeded["chat_id"]), str(seeded["artifact_id"])
    e2e_resource_ledger.register("chat", chat_id)
    chat_url, app_url = f"{ui_url}/{chat_id}", f"{ui_url}/apps/{artifact_id}"
    warm_ui_route("/")
    warm_ui_route(f"/{chat_id}")
    warm_ui_route(f"/apps/{artifact_id}")
    warm_ui_route("/artifacts")
    page_wait = page_wait_sec()

    try:
        with open_mcp_page(chat_url, timeout_ms=120_000) as (client, page):
            dismiss_blocking_modals(client, page)
            client.evaluate(page, DISMISS_MIGRATION_JS, timeout_sec=15.0)
            wait_for_react_e2e_bridge(client, page, timeout_sec=page_wait, page_url=chat_url)
            attached = client.evaluate(page, ATTACH_CHAT_JS.replace("__CHAT_ID__", json.dumps(chat_id)), timeout_sec=page_wait)
            assert isinstance(attached, dict) and attached.get("ok") is True, attached

            # Install from the card: the app's page opens with the permission question already asked.
            installed = client.evaluate(page, INSTALL_FROM_CARD_JS, timeout_sec=120.0)
            assert isinstance(installed, dict) and installed.get("ok") is True, installed
            assert installed["artifactId"] == artifact_id, installed
            assert artifact_id in installed_ids(api_url)

            dialog = wait_for_state(client, page, DIALOG_STATE_JS, timeout_sec=page_wait, page_url=app_url)
            assert dialog["hosts"] == ["example.com"], dialog
            assert dialog["checked"] == ["true", "true"], dialog

            # "Not now" decides nothing: the capabilities stay off and the app's calls are refused.
            click_dialog_button(client, page, NOT_NOW)
            wait_for_state(client, page, NO_DIALOG_JS, timeout_sec=30.0, page_url=app_url)
            wait_for_banner(client, page, app_url, click=False)
            undecided = run_app(client, page, 1)
            assert undecided["outcomes"] == [
                "notify:permission_denied",
                "fetch-declared:permission_denied",
                "fetch-undeclared:permission_denied",
            ], undecided
            assert undecided["toastSeen"] is False, undecided
            assert app_detail(api_url, artifact_id)["grants"] == []

            # The banner reopens the question; saving allows what the app asked for.
            wait_for_banner(client, page, app_url, click=True)
            wait_for_state(client, page, DIALOG_STATE_JS, timeout_sec=30.0, page_url=app_url)
            click_dialog_button(client, page, SAVE)
            wait_for_state(client, page, NO_DIALOG_JS, timeout_sec=30.0, page_url=app_url)
            stored = app_detail(api_url, artifact_id)["grants"]
            assert isinstance(stored, list)
            by_capability = {str(grant["capability"]): grant for grant in stored}
            assert by_capability["notify"]["decision"] == "allow", stored
            assert by_capability["fetch"]["decision"] == "allow" and by_capability["fetch"]["scope"] == ["example.com"], stored

            # Allowed: the notification is shown, the declared host answers through the server, the other host is refused.
            allowed = run_app(client, page, 2)
            assert allowed["outcomes"] == ["notify:ok", "fetch-declared:ok", "fetch-undeclared:host_not_granted"], allowed
            assert allowed["toastSeen"] is True, allowed

            # Each round left its own rows: the host logged the first round's refusals and the notification it showed,
            # the server logged the second round's fetches.
            assert activity_counts(api_url, artifact_id) == Counter(
                {
                    ("notify", "denied", None): 1,
                    ("notify", "ok", None): 1,
                    ("fetch", "denied", "example.com"): 1,
                    ("fetch", "denied", "example.org"): 2,
                    ("fetch", "ok", "example.com"): 1,
                }
            )

            # Uninstall from the app menu: the app and its page are gone, the artifact stays.
            open_menu_item(client, page, UNINSTALL_ITEM)
            removed = client.evaluate(page, CONFIRM_UNINSTALL_JS, timeout_sec=90.0)
            assert isinstance(removed, dict) and removed.get("ok") is True, removed
        assert artifact_id not in installed_ids(api_url)
        assert http_json_once("GET", f"{api_url}{APPS_PATH}/{artifact_id}")[0] == 404
    finally:
        http_json("DELETE", f"{api_url}{APPS_PATH}/{artifact_id}")
