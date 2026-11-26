"""In-page scripts and API reads shared by the installed-app Chrome E2E tests.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: Chrome MCP CDP test infrastructure, effect-guarded HTTP)
- e2e_core.shared_ui_hydrate (POS: whether parallel runs queue their first page load)

[OUTPUT]
- page_wait_sec: how long a page may take to answer on its first visit
- ATTACH_CHAT_JS / INSTALL_FROM_CARD_JS: open a chat and press the install button of an HTML artifact card
- DIALOG_STATE_JS / NO_DIALOG_JS / click_dialog_button: read and answer the app's permission dialog
- api_data / app_detail / activity_counts / installed_ids: the installed-app API as the host page sees it

[POS]
Shared by the deterministic app test and the real-model app test. The scripts drive the product's own buttons and
dialogs (the UI is bilingual, so labels are matched in both languages) and never touch component state.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import cast

from e2e_core.shared_ui_hydrate import parallel_shared_ui_hydrate_queue_enabled

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, http_json

APPS_PATH = "/api/v1/artifact-apps"


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
