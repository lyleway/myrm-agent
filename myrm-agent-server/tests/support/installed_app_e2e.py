"""In-page scripts and API reads shared by the installed-app Chrome E2E tests.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: Chrome MCP CDP test infrastructure, effect-guarded HTTP)
- e2e_core.shared_ui_hydrate (POS: whether parallel runs queue their first page load)

[OUTPUT]
- page_wait_sec: how long a page may take to answer on its first visit
- seeded_app: a seeded chat with its app artifact, optionally installed, removed again on exit
- ATTACH_CHAT_JS / INSTALL_FROM_CARD_JS: open a chat and press the install button of an HTML artifact card
- DIALOG_STATE_JS / NO_DIALOG_JS / click_dialog_button / set_switches / open_menu_item: read and answer the app's dialogs
- run_app / wait_for_banner: make the seeded page call the bridge (one round, a burst, or one question to the model) and reach the review banner
- api_data / app_detail / activity_counts / installed_ids / put_grant: the installed-app API as the host page sees it

[POS]
Shared by the deterministic app tests and the real-model app test. The scripts drive the product's own buttons and
dialogs (the UI is bilingual, so labels are matched in both languages) and never touch component state.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from e2e_core.shared_ui_hydrate import parallel_shared_ui_hydrate_queue_enabled

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, http_json, wait_for_state
from tests.support.e2e_runtime_guard import E2EResourceLedger

APPS_PATH = "/api/v1/artifact-apps"
SEED_PATH = "/api/v1/chats/test/seed-installed-app-fixture"


def page_wait_sec() -> float:
    """A dev server compiles a route on its first visit, and parallel runs share the host: budget for both."""
    return 180.0 if parallel_shared_ui_hydrate_queue_enabled() else 120.0


DISMISS_MIGRATION_JS = """(() => {
  try {
    localStorage.setItem('myrm.proactive.dismissed.migration-discovery', 'forever');
  } catch (err) {
    return { ok: false, err: String(err) };
  }
  return { ok: true };
})()"""

ATTACH_CHAT_JS = """(async () => {
  const bridge = window.__MYRM_E2E_CHAT__;
  if (!bridge?.attachToChat) return { ok: false, err: 'no-bridge' };
  await bridge.attachToChat(__CHAT_ID__);
  return { ok: true };
})()"""

# Presses the install button of the first HTML artifact card and waits for the app's page; reports the app's id.
INSTALL_FROM_CARD_JS = """(async () => {
  const label = /^(Install as app|安装为应用)$/;
  const deadline = Date.now() + 60000;
  let button = null;
  while (Date.now() < deadline) {
    button = Array.from(document.querySelectorAll('button')).find(
      (candidate) => label.test((candidate.getAttribute('aria-label') || '').trim()),
    );
    if (button && !button.disabled) break;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  if (!button) return { ok: false, err: 'install-button-not-found', body: document.body.innerText.slice(0, 300) };
  button.click();
  while (Date.now() < deadline) {
    const match = location.pathname.match(/^\\/apps\\/([^/]+)$/);
    if (match && document.querySelector('[data-testid="app-host"]')) return { ok: true, artifactId: match[1] };
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return { ok: false, err: 'host-page-not-reached', path: location.pathname };
})()"""

DIALOG_STATE_JS = """(() => {
  const dialog = document.querySelector('[role="dialog"]');
  if (!dialog) return { ready: false };
  const switches = Array.from(dialog.querySelectorAll('[role="switch"]'));
  return {
    ready: switches.length > 0,
    checked: switches.map((item) => item.getAttribute('aria-checked')),
    hosts: Array.from(dialog.querySelectorAll('li li')).map((item) => (item.textContent || '').trim()),
  };
})()"""

NO_DIALOG_JS = '(() => ({ ready: !document.querySelector(\'[role="dialog"], [role="alertdialog"]\') }))()'

_CLICK_DIALOG_BUTTON_JS = """(() => {
  const label = new RegExp(__PATTERN__);
  const button = Array.from(document.querySelectorAll('[role="dialog"] button')).find(
    (candidate) => label.test((candidate.textContent || '').trim()),
  );
  if (!button) return { ok: false, err: 'dialog-button-not-found' };
  button.click();
  return { ok: true };
})()"""

NOT_NOW = "^(Not now|暂不决定)$"
SAVE = "^(Save|保存)$"


def click_dialog_button(client: ChromeMcpClient, page: McpPage, pattern: str) -> None:
    """Press the button of the open permission dialog whose label matches `pattern`."""
    clicked = client.evaluate(page, _CLICK_DIALOG_BUTTON_JS.replace("__PATTERN__", json.dumps(pattern)), timeout_sec=15.0)
    assert isinstance(clicked, dict) and clicked.get("ok") is True, clicked


def api_data(payload: object) -> object:
    """The `data` of a successful standard API envelope."""
    assert isinstance(payload, dict) and payload.get("success") is True, payload
    return payload["data"]


def installed_ids(api_url: str) -> set[str]:
    apps = api_data(http_json("GET", f"{api_url}{APPS_PATH}"))
    assert isinstance(apps, list), apps
    return {str(app["artifact_id"]) for app in apps}


def app_detail(api_url: str, artifact_id: str) -> dict[str, object]:
    detail = api_data(http_json("GET", f"{api_url}{APPS_PATH}/{artifact_id}"))
    assert isinstance(detail, dict), detail
    return detail


def activity_counts(api_url: str, artifact_id: str) -> Counter[tuple[str, str, str | None]]:
    """How many times each (capability, outcome, host) was logged for the app: names only, never a path or a query."""
    rows = api_data(http_json("GET", f"{api_url}{APPS_PATH}/{artifact_id}/activity"))
    assert isinstance(rows, list), rows
    return Counter(
        (str(row["capability"]), str(row["outcome"]), cast("str | None", row["detail"])) for row in rows if isinstance(row, dict)
    )


@contextmanager
def seeded_app(api_url: str, ledger: E2EResourceLedger, *, install: bool) -> Iterator[tuple[str, str]]:
    """A chat whose answer carries the seeded app page: `(chat_id, artifact_id)`. The app is uninstalled on exit."""
    seeded = http_json("POST", f"{api_url}{SEED_PATH}")
    assert isinstance(seeded, dict), seeded
    chat_id, artifact_id = str(seeded["chat_id"]), str(seeded["artifact_id"])
    ledger.register("chat", chat_id)
    try:
        if install:
            api_data(http_json("PUT", f"{api_url}{APPS_PATH}/{artifact_id}"))
        yield chat_id, artifact_id
    finally:
        http_json("DELETE", f"{api_url}{APPS_PATH}/{artifact_id}")


def put_grant(api_url: str, artifact_id: str, capability: str, decision: str, scope: list[str]) -> None:
    """Record a decision the way the permission dialog does."""
    version = app_detail(api_url, artifact_id)["latest_version"]
    assert isinstance(version, dict), version
    body = {"decision": decision, "scope": scope, "version_sha256": version["sha256_hash"]}
    api_data(http_json("PUT", f"{api_url}{APPS_PATH}/{artifact_id}/grants/{capability}", body=body))


REVIEW_BANNER_JS = """(() => {
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
  const answer = (type, predicate, waitMs) => new Promise((resolve) => {
    const onMessage = (event) => {
      if (event.source !== frame.contentWindow || event.data?.type !== type || !predicate(event.data)) return;
      window.removeEventListener('message', onMessage);
      resolve(event.data);
    };
    window.addEventListener('message', onMessage);
    setTimeout(() => { window.removeEventListener('message', onMessage); resolve(null); }, waitMs);
  });
  const ponged = answer('e2e-pong', () => true, 45000);
  const pinger = setInterval(() => frame.contentWindow.postMessage({ type: 'e2e-ping' }, '*'), 400);
  const pong = await ponged;
  clearInterval(pinger);
  if (!pong) return { ok: false, err: 'page-not-ready' };
  const result = answer('e2e-result', (data) => data.round === round, __RESULT_WAIT_MS__);
  frame.contentWindow.postMessage({ ...__MESSAGE__, round }, '*');
  const reply = await result;
  return { ok: reply !== null, outcomes: reply?.outcomes ?? null, toastSeen: window.__e2eToastSeen === true };
})()"""


def run_app(
    client: ChromeMcpClient, page: McpPage, round_number: int, *, burst: int | None = None, ask: bool = False
) -> dict[str, object]:
    """One round of the page's calls (a notification, the declared host, another host), `burst` notifications at once, or one question to the model.

    A model answers in seconds, not milliseconds, so a question gets a longer wait for the page's report.
    """
    message = {"type": "e2e-ask"} if ask else {"type": "e2e-run"} if burst is None else {"type": "e2e-burst", "count": burst}
    result_wait_ms = 120_000 if ask else 45_000
    script = (
        _RUN_APP_JS.replace("__ROUND__", str(round_number))
        .replace("__MESSAGE__", json.dumps(message))
        .replace("__RESULT_WAIT_MS__", str(result_wait_ms))
    )
    result = client.evaluate(page, script, timeout_sec=result_wait_ms / 1000 + 60.0)
    assert isinstance(result, dict) and result.get("ok") is True, result
    return result


def wait_for_banner(client: ChromeMcpClient, page: McpPage, app_url: str, *, click: bool) -> None:
    """The "Permissions to review" banner of the app page; `click` presses it to reopen the question."""
    wait_for_state(
        client, page, REVIEW_BANNER_JS.replace("__CLICK__", "true" if click else "false"), timeout_sec=30.0, page_url=app_url
    )


_SET_SWITCHES_JS = """(() => {
  const switches = Array.from(document.querySelectorAll('[role="dialog"] [role="switch"]'));
  const want = __WANT__;
  if (switches.length !== want.length) return { ok: false, err: 'switch-count', found: switches.length };
  switches.forEach((item, index) => {
    if ((item.getAttribute('aria-checked') === 'true') !== want[index]) item.click();
  });
  return { ok: true };
})()"""


def set_switches(client: ChromeMcpClient, page: McpPage, want: list[bool]) -> None:
    """Put the open dialog's switches (in the order the dialog lists them) in the wanted state."""
    done = client.evaluate(page, _SET_SWITCHES_JS.replace("__WANT__", json.dumps(want)), timeout_sec=15.0)
    assert isinstance(done, dict) and done.get("ok") is True, done


# Opens the app menu of the host page and presses the item whose label matches; a Radix menu opens on pointerdown.
_OPEN_MENU_ITEM_JS = """(async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const label = new RegExp(__PATTERN__);
  const trigger = document.querySelector('[data-testid="app-host"] header button[aria-label]:last-of-type');
  if (!trigger) return { ok: false, err: 'menu-trigger-not-found' };
  trigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, pointerType: 'mouse' }));
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const item = Array.from(document.querySelectorAll('[role="menuitem"]')).find(
      (candidate) => label.test((candidate.textContent || '').trim()),
    );
    if (item) {
      item.click();
      return { ok: true };
    }
    await wait(200);
  }
  return { ok: false, err: 'menu-item-not-found' };
})()"""

PERMISSIONS_ITEM = "^(Permissions|权限)$"
UNINSTALL_ITEM = "^(Uninstall|卸载)$"


def open_menu_item(client: ChromeMcpClient, page: McpPage, pattern: str) -> None:
    opened = client.evaluate(page, _OPEN_MENU_ITEM_JS.replace("__PATTERN__", json.dumps(pattern)), timeout_sec=60.0)
    assert isinstance(opened, dict) and opened.get("ok") is True, opened


# After the menu's "Uninstall": confirm in the alert dialog and wait for the page to leave the app.
CONFIRM_UNINSTALL_JS = """(async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const deadline = Date.now() + 30000;
  let confirm = null;
  while (Date.now() < deadline && !confirm) {
    confirm = Array.from(document.querySelectorAll('[role="alertdialog"] button')).find(
      (candidate) => /^(Uninstall|卸载)$/.test((candidate.textContent || '').trim()),
    );
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

# What the open permission dialog shows under "Recent activity", one line per entry.
DIALOG_ACTIVITY_JS = """(() => {
  const section = document.querySelector('[role="dialog"] section[aria-label]');
  if (!section) return { ready: false };
  const lines = Array.from(section.querySelectorAll('li')).map((item) => (item.textContent || '').replace(/\s+/g, ' ').trim());
  return { ready: lines.length > 0, lines };
})()"""
