"""Chrome MCP helpers for the Add Service editor on the /settings/mcp page.

[INPUT]
- tests.support.chrome_mcp_e2e: page evaluation and state polling
- tests.support.chrome_mcp_settings_e2e: the reload-confirm dialog that follows every persisted change

[OUTPUT]
- DOM probes and click drivers for the Add Service modal (connection type, stdio fields, save, scan acknowledgement,
  description picker) and ``confirm_reload_dialog_after_save``, which settles a save up to the reload confirm

[POS]
Server pytest-only helper shared by the MCP settings Chrome E2E tests.
"""

from __future__ import annotations

import json
import time

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
    raise AssertionError(f"reload confirm dialog after save did not appear: {last}")
