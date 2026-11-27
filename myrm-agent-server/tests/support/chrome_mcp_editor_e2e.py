"""Chrome MCP helpers for the Add Service editor on the /settings/mcp page.

[INPUT]
- tests.support.chrome_mcp_e2e: page evaluation and state polling
- tests.support.chrome_mcp_settings_e2e: the reload-confirm dialog that follows every persisted change

[OUTPUT]
- DOM probes and click drivers for the Add Service modal (connection type, stdio fields, save, scan acknowledgement,
  description picker, live scan findings, blocked-save message), ``open_stdio_add_service_editor`` and
  ``fill_editor_fields`` to prepare a stdio service, and ``confirm_reload_dialog_after_save``, which settles a save
  up to the reload confirm

[POS]
Server pytest-only helper shared by the MCP settings Chrome E2E tests.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, wait_for_state
from tests.support.chrome_mcp_settings_e2e import RELOAD_DIALOG_STATE_JS, confirm_reload_dialog

ADD_EDITOR_READY_JS = """(() => {
  const modal = document.querySelector('.fixed.inset-0');
  const heading = Array.from(modal?.querySelectorAll('h3') || []).find((h) =>
    /Add Service|添加服务/i.test(
      (h.textContent || '').trim()
    )
  );
  const nameField = modal?.querySelector('input');
  return { ready: !!heading && !!nameField };
})()"""

CLICK_ADD_SERVICE_BUTTON_JS = """(() => {
  const btn = Array.from(document.querySelectorAll('button')).find((b) =>
    /Add Service|添加服务/i.test((b.textContent || '').trim())
  );
  if (!btn) return { ok: false, err: 'add-button-not-found' };
  btn.click();
  return { ok: true };
})()"""

OPEN_CONNECTION_TYPE_JS = """(() => {
  const modal = document.querySelector('.fixed.inset-0');
  if (!modal) return { ok: false, err: 'add-modal-not-found' };
  const typeLabel = Array.from(modal.querySelectorAll('label, p.text-sm')).find((node) =>
    /Connection Type|连接类型/i.test(
      (node.textContent || '').replace(/\\s+/g, ' ').trim()
    )
  );
  const trigger = typeLabel?.closest('.flex.flex-col')?.querySelector('button[type="button"]') || null;
  if (!trigger) return { ok: false, err: 'connection-type-trigger-not-found' };
  const findStdio = () =>
    Array.from(modal.querySelectorAll('button[type="button"]')).find((b) => {
      if (b === trigger) return false;
      return Array.from(b.querySelectorAll('span')).some(
        (s) => (s.textContent || '').trim().toUpperCase() === 'STDIO'
      );
    }) || null;
  if (findStdio()) {
    return { ok: true, alreadyOpen: true };
  }
  trigger.click();
  return { ok: true, alreadyOpen: false };
})()"""

CLICK_STDIO_OPTION_JS = """(() => {
  const modal = document.querySelector('.fixed.inset-0');
  if (!modal) return { ok: false, err: 'add-modal-not-found' };
  const typeLabel = Array.from(modal.querySelectorAll('label, p.text-sm')).find((node) =>
    /Connection Type|连接类型/i.test(
      (node.textContent || '').replace(/\\s+/g, ' ').trim()
    )
  );
  const trigger = typeLabel?.closest('.flex.flex-col')?.querySelector('button[type="button"]') || null;
  const stdioOption = Array.from(modal.querySelectorAll('button[type="button"]')).find((b) => {
    if (b === trigger) return false;
    return Array.from(b.querySelectorAll('span')).some(
      (s) => (s.textContent || '').trim().toUpperCase() === 'STDIO'
    );
  });
  if (!stdioOption) return { ok: false, err: 'stdio-option-not-found' };
  stdioOption.click();
  return { ok: true };
})()"""

STDIO_FIELDS_READY_JS = """(() => {
  const modal = document.querySelector('.fixed.inset-0');
  const commandLabel = Array.from(modal?.querySelectorAll('label, p.text-sm') || []).find((node) =>
    /Command|命令/i.test((node.textContent || '').replace(/\\s+/g, ' ').trim())
  );
  return { ready: !!commandLabel };
})()"""


def fill_input_by_label_js(label_pattern: str, value: str) -> str:
    escaped_value = json.dumps(value)
    escaped_pattern = json.dumps(label_pattern)
    return f"""(() => {{
  const modal = document.querySelector('.fixed.inset-0');
  if (!modal) return {{ ok: false, err: 'add-modal-not-found' }};
  const pattern = new RegExp({escaped_pattern}, 'i');
  const candidates = Array.from(modal.querySelectorAll('label, p.text-sm'));
  const label = candidates.find((node) => {{
    const text = (node.textContent || '').replace(/\\s+/g, ' ').trim();
    return pattern.test(text);
  }});
  if (!label) return {{ ok: false, err: 'label-not-found' }};
  const container = label.closest('.flex.flex-col') || label.parentElement;
  const el = container?.querySelector('input, textarea');
  if (!el) return {{ ok: false, err: 'input-not-found' }};
  const proto = el instanceof HTMLTextAreaElement
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  if (!setter) return {{ ok: false, err: 'setter-not-found' }};
  setter.call(el, {escaped_value});
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return {{ ok: true }};
}})()"""


CLICK_SAVE_CONFIG_JS = """(() => {
  const btn = Array.from(document.querySelectorAll('button')).find((b) =>
    /Save Configuration|保存配置/i.test((b.textContent || '').trim())
  );
  if (!btn) return { ok: false, err: 'save-config-not-found' };
  btn.click();
  return { ok: true };
})()"""

# The editor scans the form 500 ms after the last edit and lists what it found while the modal is open.
EDITOR_SCAN_FINDINGS_JS = """(() => {
  const items = Array.from(document.querySelectorAll('.fixed.inset-0 ul.text-amber-700 li'));
  const text = items.map((li) => (li.textContent || '').trim()).join('\\n');
  return { ready: items.length > 0, text };
})()"""

# A save the security gate refuses leaves the modal open and shows the verdict in its error box.
EDITOR_BLOCKED_MESSAGE_JS = """(() => {
  const text = (document.querySelector('.fixed.inset-0 p.text-red-600')?.textContent || '').trim();
  return { ready: text.length > 0, text };
})()"""

_MODAL_VALIDATION_ERROR_JS = """(() => {
  const modal = document.querySelector('.fixed.inset-0');
  if (!modal) return { ready: true, hasError: false };
  const errBox = modal.querySelector('.text-red-600, .text-red-400');
  if (!errBox) return { ready: true, hasError: false };
  return {
    ready: false,
    hasError: true,
    error: (errBox.textContent || '').trim().slice(0, 500),
  };
})()"""

_ADD_MODAL_OPEN_JS = """(() => {
  const modal = document.querySelector('.fixed.inset-0');
  const heading = Array.from(modal?.querySelectorAll('h3') || []).find((h) =>
    /Add Service|添加服务/i.test(
      (h.textContent || '').trim()
    )
  );
  return { ready: !!heading };
})()"""

_SAVE_VALIDATION_IDLE_JS = """(() => {
  const allBtns = Array.from(document.querySelectorAll('button'));
  const saveBtn = allBtns.find((b) =>
    /Save Configuration|保存配置/i.test((b.textContent || '').trim())
  );
  const validatingBtn = allBtns.find((b) =>
    /Validating|验证中/i.test((b.textContent || '').trim())
  );
  if (!saveBtn && validatingBtn) {
    return { ready: false, validating: true, text: (validatingBtn.textContent || '').trim() };
  }
  if (!saveBtn) return { ready: true, phase: 'no-save-btn' };
  const text = (saveBtn.textContent || '').trim();
  const validating = saveBtn.disabled || /Validating|验证/i.test(text);
  return { ready: !validating, validating, text };
})()"""

# What a save that never reached the reload confirm left on the page: whether the editor is still open with an error,
# which dialogs are up, and the page age (a small value after a long wait means the page reloaded underneath the save).
_SAVE_PAGE_STATE_JS = """(() => {
  const clip = (node) => (node?.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200);
  const modal = document.querySelector('.fixed.inset-0');
  return {
    path: location.pathname,
    pageAgeSec: Math.round(performance.now() / 1000),
    visibility: document.visibilityState,
    modalHeading: clip(modal?.querySelector('h3')),
    modalError: clip(modal?.querySelector('p.text-red-600, p.text-red-400')),
    modalButtons: Array.from(modal?.querySelectorAll('button') || []).map(clip).filter(Boolean).slice(0, 6),
    dialogs: Array.from(document.querySelectorAll('[role="alertdialog"], [role="dialog"]')).map(clip),
    bodyHead: clip(document.body).slice(0, 120),
  };
})()"""

_CLICK_SCAN_ACK_CONFIRM_JS = """(() => {
  const btn = Array.from(document.querySelectorAll('button')).find((b) =>
    /Acknowledge and continue|了解风险并继续/i.test(
      (b.textContent || '').trim()
    )
  );
  if (!btn) return { ok: false, err: 'scan-ack-not-found' };
  btn.click();
  return { ok: true };
})()"""

_CLICK_DESCRIPTION_CUSTOM_JS = """(() => {
  const heading = Array.from(document.querySelectorAll('h3')).find((h) =>
    /Choose Description|选择描述/i.test(
      (h.textContent || '').trim()
    )
  );
  if (!heading) return { ok: false, err: 'description-dialog-not-found' };
  const root = heading.closest('.fixed.inset-0') || document.body;
  const customLabel = Array.from(root.querySelectorAll('span')).find((s) =>
    /Custom Description|自定义描述/i.test(
      (s.textContent || '').trim()
    )
  );
  const btn = customLabel?.closest('button');
  if (!btn) return { ok: false, err: 'description-custom-not-found' };
  btn.click();
  return { ok: true };
})()"""


def fill_editor_fields(client: ChromeMcpClient, page: McpPage, fields: Iterable[tuple[str, str]]) -> None:
    """Fill the Add Service field found under each ``(label regex, value)`` pair."""
    for label_pattern, value in fields:
        filled = client.evaluate(page, fill_input_by_label_js(label_pattern, value), timeout_sec=15.0)
        assert isinstance(filled, dict) and filled.get("ok") is True, (label_pattern, filled)


def open_stdio_add_service_editor(client: ChromeMcpClient, page: McpPage, *, name: str) -> None:
    """Open the Add Service modal on a ready MCP page, name the service and switch it to the STDIO connection type."""
    opened = client.evaluate(page, CLICK_ADD_SERVICE_BUTTON_JS, timeout_sec=15.0)
    assert isinstance(opened, dict) and opened.get("ok") is True, opened
    editor = wait_for_state(client, page, ADD_EDITOR_READY_JS, timeout_sec=30.0)
    assert editor.get("ready") is True, json.dumps(editor, ensure_ascii=False)
    fill_editor_fields(client, page, [("Service Name|服务名称", name)])
    selected_stdio: object = {"ok": False}
    for attempt in range(4):
        opened_type = client.evaluate(page, OPEN_CONNECTION_TYPE_JS, timeout_sec=15.0)
        assert isinstance(opened_type, dict) and opened_type.get("ok") is True, opened_type
        for _ in range(10):
            time.sleep(0.15)
            selected_stdio = client.evaluate(page, CLICK_STDIO_OPTION_JS, timeout_sec=15.0)
            if isinstance(selected_stdio, dict) and selected_stdio.get("ok") is True:
                break
        if isinstance(selected_stdio, dict) and selected_stdio.get("ok") is True:
            break
        time.sleep(0.5 * (attempt + 1))
    assert isinstance(selected_stdio, dict) and selected_stdio.get("ok") is True, selected_stdio
    stdio_ready = wait_for_state(client, page, STDIO_FIELDS_READY_JS, timeout_sec=15.0)
    assert stdio_ready.get("ready") is True, json.dumps(stdio_ready, ensure_ascii=False)


def _describe_save_page(client: ChromeMcpClient, page: McpPage) -> object:
    """The page as a save left it, for the failure message of a save that never reached the reload confirm."""
    try:
        return client.evaluate(page, _SAVE_PAGE_STATE_JS, timeout_sec=10.0)
    except (RuntimeError, TimeoutError) as exc:
        return f"page unreadable: {exc}"


def confirm_reload_dialog_after_save(client: ChromeMcpClient, page: McpPage, *, timeout_sec: float = 120.0) -> None:
    """Wait for async MCP save validation, intermediate ack dialogs, then reload confirm."""
    wait_for_state(client, page, _SAVE_VALIDATION_IDLE_JS, timeout_sec=90.0)
    err = client.evaluate(page, _MODAL_VALIDATION_ERROR_JS, timeout_sec=10.0)
    if isinstance(err, dict) and err.get("hasError") is True:
        raise AssertionError(f"MCP add/save validation failed: {err.get('error')!r}")
    deadline = time.monotonic() + timeout_sec
    last: dict[str, object] = {}
    save_retries = 0
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        handled_intermediate = False
        for intermediate_js in (
            _CLICK_SCAN_ACK_CONFIRM_JS,
            _CLICK_DESCRIPTION_CUSTOM_JS,
        ):
            try:
                result = client.evaluate(page, intermediate_js, timeout_sec=min(8.0, remaining))
            except (RuntimeError, TimeoutError):
                result = {"ok": False}
            if isinstance(result, dict) and result.get("ok") is True:
                handled_intermediate = True
                time.sleep(0.6)
                break
        if handled_intermediate:
            continue
        try:
            raw = client.evaluate(page, RELOAD_DIALOG_STATE_JS, timeout_sec=min(10.0, remaining))
        except (RuntimeError, TimeoutError):
            raw = last
        last = raw if isinstance(raw, dict) else {"value": raw}
        if last.get("ready") is True:
            confirm_reload_dialog(client, page, timeout_sec=min(45.0, remaining))
            return
        try:
            modal_open = client.evaluate(page, _ADD_MODAL_OPEN_JS, timeout_sec=min(8.0, remaining))
        except (RuntimeError, TimeoutError):
            modal_open = {"ready": False}
        if save_retries < 2 and isinstance(modal_open, dict) and modal_open.get("ready") is True:
            save_retries += 1
            retried = client.evaluate(page, CLICK_SAVE_CONFIG_JS, timeout_sec=10.0)
            assert isinstance(retried, dict) and retried.get("ok") is True, retried
            wait_for_state(client, page, _SAVE_VALIDATION_IDLE_JS, timeout_sec=min(60.0, remaining))
            continue
        time.sleep(0.4)
    raise AssertionError(f"reload confirm dialog after save did not appear: {last}; page: {_describe_save_page(client, page)}")
