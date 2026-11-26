"""Chrome E2E: MCP settings reload-confirm dialog (M6 persistConfigs gate)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from tests.support.chrome_mcp_e2e import (
    dismiss_blocking_modals,
    get_e2e_api_url,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)
from tests.support.chrome_mcp_editor_e2e import (
    ADD_EDITOR_READY_JS,
    CLICK_ADD_SERVICE_BUTTON_JS,
    CLICK_SAVE_CONFIG_JS,
    CLICK_STDIO_OPTION_JS,
    OPEN_CONNECTION_TYPE_JS,
    STDIO_FIELDS_READY_JS,
    confirm_reload_dialog_after_save,
    fill_input_by_label_js,
)
from tests.support.chrome_mcp_settings_e2e import (
    CLICK_DIALOG_CANCEL_JS,
    CLICK_IMPORT_JSON_BUTTON_JS,
    CLICK_IMPORT_SUBMIT_JS,
    MCP_IMPORT_MODAL_READY_JS,
    MCP_PAGE_READY_JS,
    RELOAD_DIALOG_CLOSED_JS,
    RELOAD_DIALOG_STATE_JS,
    confirm_reload_dialog,
    mcp_page_state_js,
    put_mcp_configs,
    reload_mcp_settings_page,
    seed_empty_mcp_configs,
    server_config_in_api,
    server_exists_in_api,
    set_import_textarea_js,
    wait_for_server_absent,
    wait_for_server_present,
    wait_until,
)

_PROBE_SERVER_NAME = "e2e-reload-probe"
_IMPORT_SERVER_NAME = "e2e-import-probe"
_ADD_SERVER_NAME = "e2e-add-probe"
_E2E_STDIO_MCP_STUB = Path(__file__).resolve().parents[1] / "support" / "e2e_minimal_stdio_mcp_server.py"

_MCP_PAGE_HAS_PROBE_JS = mcp_page_state_js(_PROBE_SERVER_NAME)
_MCP_PAGE_HAS_ADD_PROBE_JS = mcp_page_state_js(_ADD_SERVER_NAME)

_TOGGLE_PROBE_SWITCH_JS = """(() => {
  const nameNode = Array.from(document.querySelectorAll('p')).find((p) =>
    (p.textContent || '').trim() === 'e2e-reload-probe'
  );
  if (!nameNode) return { ok: false, err: 'probe-row-not-found' };
  const row = nameNode.closest('.group') || nameNode.parentElement?.parentElement?.parentElement;
  if (!row) return { ok: false, err: 'probe-row-container-not-found' };
  const switchBtn = row.querySelector('button[role="switch"]');
  if (!switchBtn) return { ok: false, err: 'switch-not-found' };
  switchBtn.click();
  return { ok: true };
})()"""

_CLICK_PROBE_DELETE_JS = """(() => {
  const nameNode = Array.from(document.querySelectorAll('p')).find((p) =>
    (p.textContent || '').trim() === 'e2e-reload-probe'
  );
  if (!nameNode) return { ok: false, err: 'probe-row-not-found' };
  const row = nameNode.closest('.group') || nameNode.parentElement?.parentElement?.parentElement;
  if (!row) return { ok: false, err: 'probe-row-container-not-found' };
  const trashIcon = row.querySelector('.text-red-500');
  const trashBtn = trashIcon?.closest('button');
  if (!trashBtn) return { ok: false, err: 'delete-not-found' };
  trashBtn.click();
  return { ok: true };
})()"""

_DELETE_DIALOG_STATE_JS = """(() => {
  const text = document.body?.innerText || '';
  const titleMatch = /Confirm Delete|确认删除/i.test(text);
  const buttons = Array.from(document.querySelectorAll('button')).map((b) => (b.textContent || '').trim());
  const hasCancel = buttons.some((b) => /^(取消|Cancel)$/i.test(b));
  const hasConfirm = buttons.some((b) => /^(Confirm Delete|确认删除)$/i.test(b));
  return { ready: titleMatch && hasCancel && hasConfirm, titleMatch, hasCancel, hasConfirm };
})()"""

_CLICK_DELETE_CONFIRM_JS = """(() => {
  const btn = Array.from(document.querySelectorAll('button')).find((b) =>
    /^(Confirm Delete|确认删除)$/i.test((b.textContent || '').trim())
  );
  if (!btn) return { ok: false, err: 'delete-confirm-not-found' };
  btn.click();
  return { ok: true };
})()"""


def _probe_enabled_in_api() -> bool:
    return (server_config_in_api(_PROBE_SERVER_NAME) or {}).get("enabled") is True


def _seed_probe_mcp_server(*, enabled: bool = True) -> None:
    probe = {
        "name": _PROBE_SERVER_NAME,
        "type": "stdio",
        "command": sys.executable,
        "args": ["-c", "pass"],
        "description": "E2E MCP reload confirm probe",
        "enabled": enabled,
        "headers": {},
        "extra_params": {},
    }
    put_mcp_configs([probe])
    wait_until(
        lambda: server_exists_in_api(_PROBE_SERVER_NAME) and (not enabled or _probe_enabled_in_api()),
        timeout_sec=20.0,
        failure="failed to seed MCP probe server",
    )


def _wait_for_probe_disabled(timeout_sec: float = 45.0) -> None:
    wait_until(
        lambda: not _probe_enabled_in_api(),
        timeout_sec=timeout_sec,
        failure="confirm must persist disabled MCP server",
    )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE", access_scope="GLOBAL_WRITE", workload="STANDARD", private_reason="global_write_non_namespace"
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_mcp_reload_confirm_dialog_all_paths_single_session() -> None:
    """Single SHPOIB session: toggle cancel/confirm, delete, import, add/save."""
    api_url = get_e2e_api_url()
    prepare_e2e_ui_session(api_url)

    import_payload = json.dumps(
        {
            "mcpServers": {
                _IMPORT_SERVER_NAME: {
                    "command": sys.executable,
                    "args": ["-c", "pass"],
                    "description": "E2E import probe server",
                }
            }
        }
    )

    warm_ui_route("/settings/mcp")
    with open_settings_subroute("/settings/mcp", timeout_ms=120_000) as (client, page):
        # --- Path 1: disable toggle cancel + confirm ---
        _seed_probe_mcp_server()
        assert _probe_enabled_in_api() is True
        reload_mcp_settings_page(client, page)
        dismiss_blocking_modals(client, page)
        ready = wait_for_state(client, page, _MCP_PAGE_HAS_PROBE_JS, timeout_sec=90.0)
        assert ready.get("ready") is True, json.dumps(ready, ensure_ascii=False)
        wait_for_state(client, page, _MCP_PAGE_HAS_PROBE_JS, timeout_sec=30.0)

        toggled = client.evaluate(page, _TOGGLE_PROBE_SWITCH_JS, timeout_sec=15.0)
        assert isinstance(toggled, dict) and toggled.get("ok") is True, toggled
        dialog = wait_for_state(client, page, RELOAD_DIALOG_STATE_JS, timeout_sec=30.0)
        assert dialog.get("ready") is True, json.dumps(dialog, ensure_ascii=False)
        cancelled = client.evaluate(page, CLICK_DIALOG_CANCEL_JS, timeout_sec=10.0)
        assert isinstance(cancelled, dict) and cancelled.get("ok") is True, cancelled
        wait_for_state(client, page, RELOAD_DIALOG_CLOSED_JS, timeout_sec=15.0)
        assert _probe_enabled_in_api() is True, "cancel must not persist disable"

        ready_after_cancel = wait_for_state(client, page, _MCP_PAGE_HAS_PROBE_JS, timeout_sec=30.0)
        assert ready_after_cancel.get("ready") is True, json.dumps(ready_after_cancel, ensure_ascii=False)
        toggled_again = client.evaluate(page, _TOGGLE_PROBE_SWITCH_JS, timeout_sec=15.0)
        assert isinstance(toggled_again, dict) and toggled_again.get("ok") is True, toggled_again
        confirm_reload_dialog(client, page)
        _wait_for_probe_disabled(timeout_sec=45.0)

        # --- Path 2: delete ---
        _seed_probe_mcp_server()
        reload_mcp_settings_page(client, page)
        ready_delete = wait_for_state(client, page, _MCP_PAGE_HAS_PROBE_JS, timeout_sec=90.0)
        assert ready_delete.get("ready") is True, json.dumps(ready_delete, ensure_ascii=False)

        clicked_delete = client.evaluate(page, _CLICK_PROBE_DELETE_JS, timeout_sec=15.0)
        assert isinstance(clicked_delete, dict) and clicked_delete.get("ok") is True, clicked_delete
        delete_dialog = wait_for_state(client, page, _DELETE_DIALOG_STATE_JS, timeout_sec=20.0)
        assert delete_dialog.get("ready") is True, json.dumps(delete_dialog, ensure_ascii=False)
        confirmed_delete = client.evaluate(page, _CLICK_DELETE_CONFIRM_JS, timeout_sec=10.0)
        assert isinstance(confirmed_delete, dict) and confirmed_delete.get("ok") is True, confirmed_delete
        confirm_reload_dialog(client, page)
        wait_for_server_absent(_PROBE_SERVER_NAME)

        # --- Path 3: import JSON ---
        seed_empty_mcp_configs()
        reload_mcp_settings_page(client, page)
        ready_import = wait_for_state(client, page, MCP_PAGE_READY_JS, timeout_sec=90.0)
        assert ready_import.get("ready") is True, json.dumps(ready_import, ensure_ascii=False)

        opened = client.evaluate(page, CLICK_IMPORT_JSON_BUTTON_JS, timeout_sec=15.0)
        assert isinstance(opened, dict) and opened.get("ok") is True, opened
        import_modal = wait_for_state(client, page, MCP_IMPORT_MODAL_READY_JS, timeout_sec=30.0)
        assert import_modal.get("ready") is True, json.dumps(import_modal, ensure_ascii=False)
        filled = client.evaluate(page, set_import_textarea_js(import_payload), timeout_sec=15.0)
        assert isinstance(filled, dict) and filled.get("ok") is True, filled
        submitted = client.evaluate(page, CLICK_IMPORT_SUBMIT_JS, timeout_sec=15.0)
        assert isinstance(submitted, dict) and submitted.get("ok") is True, submitted
        confirm_reload_dialog(client, page, timeout_sec=90.0)
        wait_for_server_present(_IMPORT_SERVER_NAME)

        # --- Path 4: add/save form ---
        seed_empty_mcp_configs()
        reload_mcp_settings_page(client, page)
        ready_add = wait_for_state(client, page, MCP_PAGE_READY_JS, timeout_sec=90.0)
        assert ready_add.get("ready") is True, json.dumps(ready_add, ensure_ascii=False)

        opened_add = client.evaluate(page, CLICK_ADD_SERVICE_BUTTON_JS, timeout_sec=15.0)
        assert isinstance(opened_add, dict) and opened_add.get("ok") is True, opened_add
        add_editor = wait_for_state(client, page, ADD_EDITOR_READY_JS, timeout_sec=30.0)
        assert add_editor.get("ready") is True, json.dumps(add_editor, ensure_ascii=False)
        name_filled = client.evaluate(
            page,
            fill_input_by_label_js(
                "Service Name|服务名称",
                _ADD_SERVER_NAME,
            ),
            timeout_sec=15.0,
        )
        assert isinstance(name_filled, dict) and name_filled.get("ok") is True, name_filled
        selected_stdio: dict[str, object] = {"ok": False}
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
        for label_pattern, value in (
            ("Command|命令", sys.executable),
            ("Arguments|参数", str(_E2E_STDIO_MCP_STUB)),
            ("Description|描述", "E2E add probe server"),
            ("Cache TTL|缓存时长", "60"),
            ("Cacheable tools|可缓存工具", "ping"),
        ):
            filled_field = client.evaluate(
                page,
                fill_input_by_label_js(label_pattern, value),
                timeout_sec=15.0,
            )
            assert isinstance(filled_field, dict) and filled_field.get("ok") is True, (
                label_pattern,
                filled_field,
            )
        saved = client.evaluate(page, CLICK_SAVE_CONFIG_JS, timeout_sec=15.0)
        assert isinstance(saved, dict) and saved.get("ok") is True, saved
        confirm_reload_dialog_after_save(client, page, timeout_sec=120.0)
        wait_for_server_present(_ADD_SERVER_NAME)
        saved_config = server_config_in_api(_ADD_SERVER_NAME)
        assert saved_config is not None, "saved MCP config missing from API"
        assert saved_config.get("cacheTtlSeconds") == 60, saved_config
        assert saved_config.get("cacheableTools") == ["ping"], saved_config
        ui_ready = wait_for_state(client, page, _MCP_PAGE_HAS_ADD_PROBE_JS, timeout_sec=60.0)
        assert ui_ready.get("ready") is True, json.dumps(ui_ready, ensure_ascii=False)
