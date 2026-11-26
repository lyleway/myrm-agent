"""Shared Chrome MCP helpers for the /settings/mcp page.

[INPUT]
- tests.support.chrome_mcp_e2e: page evaluation, state polling, page reload and the E2E backend HTTP client

[OUTPUT]
- Page probes and the Import JSON modal driver, the persisted MCP configs read and written through the backend API,
  and the reload-confirm dialog that gates every persisted change

[POS]
Server pytest-only helper shared by the MCP settings Chrome E2E tests.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    dismiss_blocking_modals,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    reload_mcp_page,
    wait_for_state,
)


def mcp_page_state_js(required_text: str | None = None) -> str:
    """Probe the MCP settings section; failure payload says *why* it is not ready.

    ``deferredBusy`` mirrors ``SettingsLayout``'s ``aria-busy`` (deferred locale bundle still
    loading) and ``mainSample`` is the ``<main>`` text, so a stuck skeleton, a degraded locale
    and a missing server row are distinguishable instead of all showing only the sidebar.
    """
    has_required = f"text.includes({json.dumps(required_text)})" if required_text else "true"
    return f"""(() => {{
  const text = document.body?.innerText || '';
  const hasHeading = /MCP 服务配置|MCP Service/i.test(text);
  const hasRequired = {has_required};
  const layout = document.querySelector('[data-testid="settings-layout"]');
  return {{
    ready: hasHeading && hasRequired,
    hasHeading,
    hasRequired,
    deferredBusy: layout?.getAttribute('aria-busy') === 'true',
    mainSample: (document.querySelector('main')?.innerText || '').slice(0, 300),
  }};
}})()"""


MCP_PAGE_READY_JS = mcp_page_state_js()

APP_LAYOUT_READY_JS = """(() => ({
  ready: !!document.querySelector('[data-testid="app-layout"]'),
}))()"""

MCP_IMPORT_MODAL_READY_JS = """(() => {
  const heading = Array.from(document.querySelectorAll('h3')).find((h) =>
    /Import MCP Configuration|导入 MCP 配置/i.test(
      (h.textContent || '').trim()
    )
  );
  const modal = heading?.closest('.fixed.inset-0');
  const textarea = modal?.querySelector('textarea');
  return { ready: !!textarea, err: textarea ? null : 'import-textarea-not-found' };
})()"""

CLICK_IMPORT_JSON_BUTTON_JS = """(() => {
  const btn = Array.from(document.querySelectorAll('button')).find((b) =>
    /Import JSON|导入 JSON/i.test((b.textContent || '').trim())
  );
  if (!btn) return { ok: false, err: 'import-button-not-found' };
  btn.click();
  return { ok: true };
})()"""

CLICK_IMPORT_SUBMIT_JS = """(() => {
  const modal = Array.from(document.querySelectorAll('h3')).find((h) =>
    /Import MCP Configuration|导入 MCP 配置/i.test(
      (h.textContent || '').trim()
    )
  )?.closest('.fixed.inset-0');
  const scope = modal || document;
  const btn = Array.from(scope.querySelectorAll('button')).find((b) =>
    /^(Import|导入)$/i.test((b.textContent || '').trim())
  );
  if (!btn) return { ok: false, err: 'import-submit-not-found' };
  btn.click();
  return { ok: true };
})()"""

# The importer shows the scan verdict of a refused configuration as the modal's destructive paragraph.
MCP_IMPORT_ERROR_JS = """(() => {
  const modal = Array.from(document.querySelectorAll('h3')).find((h) =>
    /Import MCP Configuration|导入 MCP 配置/i.test(
      (h.textContent || '').trim()
    )
  )?.closest('.fixed.inset-0');
  const text = (modal?.querySelector('p.text-destructive-text')?.textContent || '').trim();
  return { ready: text.length > 0, text };
})()"""


def set_import_textarea_js(value: str) -> str:
    escaped = json.dumps(value)
    return f"""(() => {{
  const heading = Array.from(document.querySelectorAll('h3')).find((h) =>
    /Import MCP Configuration|导入 MCP 配置/i.test(
      (h.textContent || '').trim()
    )
  );
  const modal = heading?.closest('.fixed.inset-0');
  const el = modal?.querySelector('textarea');
  if (!el) return {{ ok: false, err: 'import-textarea-not-found' }};
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
  if (!setter) return {{ ok: false, err: 'setter-not-found' }};
  setter.call(el, {escaped});
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return {{ ok: true }};
}})()"""


RELOAD_DIALOG_STATE_JS = """(() => {
  const alert = Array.from(document.querySelectorAll('[role="alertdialog"]')).find((node) => {
    const text = node.textContent || '';
    return /重新加载 MCP 工具|Reload MCP tools/i.test(text);
  });
  if (!alert) {
    return {
      ready: false,
      titleMatch: false,
      descMatch: false,
      hasCancel: false,
      hasConfirm: false,
      buttons: [],
    };
  }
  const text = alert.textContent || '';
  const titleMatch = /重新加载 MCP 工具|Reload MCP tools/i.test(text);
  const descMatch = /提示词缓存|prompt-cache|Prompt-Cache/i.test(text);
  const buttons = Array.from(alert.querySelectorAll('button')).map((b) =>
    (b.textContent || '').trim()
  );
  const hasCancel = buttons.some((b) => /^(取消|Cancel)$/i.test(b));
  const hasConfirm = buttons.some((b) =>
    /^(保存并应用|Save and apply)$/i.test(b)
  );
  return {
    ready: titleMatch && descMatch && hasCancel && hasConfirm,
    titleMatch,
    descMatch,
    hasCancel,
    hasConfirm,
    buttons,
  };
})()"""

RELOAD_DIALOG_CLOSED_JS = """(() => {
  const alert = Array.from(document.querySelectorAll('[role="alertdialog"]')).find((node) => {
    const text = node.textContent || '';
    return /重新加载 MCP 工具|Reload MCP tools/i.test(text);
  });
  return { ready: !alert };
})()"""

CLICK_DIALOG_CANCEL_JS = """(() => {
  const alert = Array.from(document.querySelectorAll('[role="alertdialog"]')).find((node) => {
    const text = node.textContent || '';
    return /重新加载 MCP 工具|Reload MCP tools/i.test(text);
  });
  const scope = alert || document;
  const btn = Array.from(scope.querySelectorAll('button')).find((b) =>
    /^(取消|Cancel)$/i.test((b.textContent || '').trim())
  );
  if (!btn) return { ok: false, err: 'cancel-not-found' };
  btn.click();
  return { ok: true };
})()"""

CLICK_DIALOG_CONFIRM_JS = """(() => {
  const alert = Array.from(document.querySelectorAll('[role="alertdialog"]')).find((node) => {
    const text = node.textContent || '';
    return /重新加载 MCP 工具|Reload MCP tools/i.test(text);
  });
  const scope = alert || document;
  const btn = Array.from(scope.querySelectorAll('button')).find((b) =>
    /^(保存并应用|Save and apply)$/i.test((b.textContent || '').trim())
  );
  if (!btn) return { ok: false, err: 'confirm-not-found' };
  btn.click();
  return { ok: true };
})()"""


def _config_url() -> str:
    return f"{get_e2e_api_url()}/api/v1/config/mcpServers"


def _fetch_record() -> dict[str, object]:
    payload = http_json("GET", _config_url())
    assert isinstance(payload, dict)
    return payload


def _persisted_configs() -> list[dict[str, object]]:
    """MCP configs as the backend stores them; empty while nothing is stored."""
    value = _fetch_record().get("value")
    configs = value.get("mcpConfigs") if isinstance(value, dict) else None
    return [item for item in configs if isinstance(item, dict)] if isinstance(configs, list) else []


def list_mcp_server_names() -> list[str]:
    return [item["name"] for item in _persisted_configs() if isinstance(item.get("name"), str)]


def server_config_in_api(name: str) -> dict[str, object] | None:
    return next((item for item in _persisted_configs() if item.get("name") == name), None)


def server_exists_in_api(name: str) -> bool:
    return server_config_in_api(name) is not None


def put_mcp_configs(configs: list[dict[str, object]]) -> None:
    http_json(
        "PUT",
        _config_url(),
        {
            "deviceId": "web",
            "expectedVersion": str(_fetch_record().get("version") or "0"),
            "value": {"mcpConfigs": configs},
        },
    )


def wait_until(predicate: Callable[[], bool], *, timeout_sec: float, failure: str) -> None:
    """Poll ``predicate`` every 0.4 s until it holds; raise ``AssertionError(failure)`` once ``timeout_sec`` passes."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.4)
    raise AssertionError(failure)


def seed_empty_mcp_configs() -> None:
    put_mcp_configs([])
    wait_until(lambda: list_mcp_server_names() == [], timeout_sec=20.0, failure="failed to seed empty MCP configs")


def wait_for_server_absent(name: str, timeout_sec: float = 20.0) -> None:
    wait_until(lambda: not server_exists_in_api(name), timeout_sec=timeout_sec, failure=f"server {name!r} still present in API")


def wait_for_server_present(name: str, timeout_sec: float = 20.0) -> None:
    wait_until(lambda: server_exists_in_api(name), timeout_sec=timeout_sec, failure=f"server {name!r} not present in API")


def confirm_reload_dialog(client: ChromeMcpClient, page: McpPage, *, timeout_sec: float = 45.0) -> None:
    dialog = wait_for_state(client, page, RELOAD_DIALOG_STATE_JS, timeout_sec=timeout_sec)
    assert dialog.get("ready") is True, json.dumps(dialog, ensure_ascii=False)
    confirmed = client.evaluate(page, CLICK_DIALOG_CONFIRM_JS, timeout_sec=10.0)
    assert isinstance(confirmed, dict) and confirmed.get("ok") is True, confirmed
    wait_for_state(client, page, RELOAD_DIALOG_CLOSED_JS, timeout_sec=30.0)


def reload_mcp_settings_page(client: ChromeMcpClient, page: McpPage) -> None:
    """Reload /settings/mcp until the app layout is back, retrying a reload that lands on a blank shell."""
    target_url = f"{get_e2e_ui_url().rstrip('/')}/settings/mcp"
    last: dict[str, object] = {}
    for attempt in range(3):
        reload_mcp_page(client, page, target_url=target_url)
        try:
            wait_for_state(client, page, APP_LAYOUT_READY_JS, timeout_sec=120.0)
            dismiss_blocking_modals(client, page)
            return
        except AssertionError as exc:
            if attempt >= 2:
                raise
            last = {"attempt": attempt + 1, "error": str(exc)}
            time.sleep(2.0 * (attempt + 1))
    raise AssertionError(f"MCP page reload did not recover: {last}")
