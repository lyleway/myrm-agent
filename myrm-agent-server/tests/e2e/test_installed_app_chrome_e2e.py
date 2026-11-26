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
    ChromeMcpClient,
    McpPage,
    dismiss_blocking_modals,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    http_json_once,
    open_mcp_page,
    prepare_e2e_ui_session,
    wait_for_react_e2e_bridge,
    wait_for_state,
    warm_ui_route,
)
from tests.support.e2e_runtime_guard import E2EResourceLedger
from tests.support.installed_app_e2e import (
    APPS_PATH,
    ATTACH_CHAT_JS,
    DIALOG_STATE_JS,
    DISMISS_MIGRATION_JS,
    INSTALL_FROM_CARD_JS,
    NO_DIALOG_JS,
    NOT_NOW,
    SAVE,
    activity_counts,
    app_detail,
    click_dialog_button,
    installed_ids,
    page_wait_sec,
)

_SEED_PATH = "/api/v1/chats/test/seed-installed-app-fixture"
_REVIEW_BANNER_JS = """(() => {
  const label = /^(Permissions to review|有权限待确认)$/;
  const banner = Array.from(document.querySelectorAll('[data-testid="app-host"] header button')).find(
    (candidate) => label.test((candidate.textContent || '').trim()),
  );
  if (banner && __CLICK__) banner.click();
  return { ready: Boolean(banner) };
})()"""

# The sandboxed page cannot be clicked from outside, so the host posts the message that stands for the user's click and
# reads the outcomes the page reports back. A notification toast is short-lived: an observer remembers it was shown.
_RUN_APP_JS = """(async () => {
  const round = __ROUND__;
  const frame = document.querySelector('[data-testid="app-host"] iframe');
  if (!frame?.contentWindow) return { ok: false, err: 'no-frame' };
  if (!window.__e2eToastObserver) {
    window.__e2eToastSeen = false;
    window.__e2eToastObserver = new MutationObserver(() => {
      if (document.body.innerText.includes('Pulse ping')) window.__e2eToastSeen = true;
    });
    window.__e2eToastObserver.observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  const answer = (type, predicate) => new Promise((resolve) => {
    const onMessage = (event) => {
      if (event.source !== frame.contentWindow || event.data?.type !== type || !predicate(event.data)) return;
      window.removeEventListener('message', onMessage);
      resolve(event.data);
    };
    window.addEventListener('message', onMessage);
    setTimeout(() => { window.removeEventListener('message', onMessage); resolve(null); }, 45000);
  });
  const ponged = answer('e2e-pong', () => true);
  const pinger = setInterval(() => frame.contentWindow.postMessage({ type: 'e2e-ping' }, '*'), 400);
  const pong = await ponged;
  clearInterval(pinger);
  if (!pong) return { ok: false, err: 'page-not-ready' };
  const result = answer('e2e-result', (data) => data.round === round);
  frame.contentWindow.postMessage({ type: 'e2e-run', round }, '*');
  const reply = await result;
  return { ok: reply !== null, outcomes: reply?.outcomes ?? null, toastSeen: window.__e2eToastSeen === true };
})()"""

_UNINSTALL_FROM_MENU_JS = """(async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const find = (selector, label) => Array.from(document.querySelectorAll(selector)).find(
    (candidate) => label.test((candidate.textContent || '').trim()),
  );
  const trigger = document.querySelector('[data-testid="app-host"] header button[aria-label]:last-of-type');
  if (!trigger) return { ok: false, err: 'menu-trigger-not-found' };
  trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }));
  const deadline = Date.now() + 30000;
  let item = null;
  while (Date.now() < deadline && !item) {
    item = find('[role="menuitem"]', /^(Uninstall|卸载)$/);
    if (!item) await wait(200);
  }
  if (!item) return { ok: false, err: 'uninstall-item-not-found' };
  item.click();
  let confirm = null;
  while (Date.now() < deadline && !confirm) {
    confirm = find('[role="alertdialog"] button', /^(Uninstall|卸载)$/);
    if (!confirm) await wait(200);
  }
  if (!confirm) return { ok: false, err: 'confirm-not-found' };
  confirm.click();
  const redirectBy = Date.now() + 60000;
  while (Date.now() < redirectBy) {
    if (location.pathname === '/artifacts') return { ok: true };
    await wait(300);
  }
  return { ok: false, err: 'not-redirected', path: location.pathname };
})()"""


def _run_app(client: ChromeMcpClient, page: McpPage, round_number: int) -> dict[str, object]:
    result = client.evaluate(page, _RUN_APP_JS.replace("__ROUND__", str(round_number)), timeout_sec=90.0)
    assert isinstance(result, dict) and result.get("ok") is True, result
    return result


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_an_installed_app_uses_the_bridge_only_for_what_the_user_allowed_chrome_e2e(
    e2e_resource_ledger: E2EResourceLedger,
) -> None:
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url().rstrip("/")
    prepare_e2e_ui_session(api_url)
    seeded = http_json("POST", f"{api_url}{_SEED_PATH}")
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
            wait_for_state(client, page, _REVIEW_BANNER_JS.replace("__CLICK__", "false"), timeout_sec=30.0, page_url=app_url)
            undecided = _run_app(client, page, 1)
            assert undecided["outcomes"] == [
                "notify:permission_denied",
                "fetch-declared:permission_denied",
                "fetch-undeclared:permission_denied",
            ], undecided
            assert undecided["toastSeen"] is False, undecided
            assert app_detail(api_url, artifact_id)["grants"] == []

            # The banner reopens the question; saving allows what the app asked for.
            wait_for_state(client, page, _REVIEW_BANNER_JS.replace("__CLICK__", "true"), timeout_sec=30.0, page_url=app_url)
            wait_for_state(client, page, DIALOG_STATE_JS, timeout_sec=30.0, page_url=app_url)
            click_dialog_button(client, page, SAVE)
            wait_for_state(client, page, NO_DIALOG_JS, timeout_sec=30.0, page_url=app_url)
            stored = app_detail(api_url, artifact_id)["grants"]
            assert isinstance(stored, list)
            by_capability = {str(grant["capability"]): grant for grant in stored}
            assert by_capability["notify"]["decision"] == "allow", stored
            assert by_capability["fetch"]["decision"] == "allow" and by_capability["fetch"]["scope"] == ["example.com"], stored

            # Allowed: the notification is shown, the declared host answers through the server, the other host is refused.
            allowed = _run_app(client, page, 2)
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
            removed = client.evaluate(page, _UNINSTALL_FROM_MENU_JS, timeout_sec=90.0)
            assert isinstance(removed, dict) and removed.get("ok") is True, removed
        assert artifact_id not in installed_ids(api_url)
        assert http_json_once("GET", f"{api_url}{APPS_PATH}/{artifact_id}")[0] == 404
    finally:
        http_json("DELETE", f"{api_url}{APPS_PATH}/{artifact_id}")
