"""Chrome E2E: the plugin import dialog reports an over-nested mcp.json and still offers the skills of the archive.

[INPUT]
- tests.support.chrome_mcp_e2e::(open_settings_subroute, wait_for_state, dismiss_blocking_modals) (POS: owned Chrome page + state polling)
- myrm_agent_harness.utils.clean_json::MAX_NESTING (POS: the deepest nesting of a document the loader accepts)

[OUTPUT]
- test_a_nested_mcp_config_is_reported_in_the_import_dialog_and_the_skill_stays: fails when the dialog answers an archive whose
  mcp.json is one level deeper than ``MAX_NESTING`` with a parse error, when the notice that no MCP server is imported is
  missing, or when the skill of the same archive is not offered.

[POS]
Browser-lane check of the path ``tests/integration`` covers without a UI: the archive is chosen in the file input of the dialog
opened from Settings > Skills, so the preview request, its diagnostics and the sentences the user reads are the product's own.
The notice is worded the same for every invalid mcp.json, so the archive's mcp.json is valid in every respect but its depth:
the value of ``capabilities`` is the one place a valid server entry takes any JSON, and the same document one level shallower
lists its server instead. The preview persists the archive as an import session under a random id, which the server sweeps
after 24 hours; nothing is installed, so that session is the node's only write.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest
from myrm_agent_harness.utils.clean_json import MAX_NESTING

from tests.support.chrome_mcp_e2e import (
    _warm_ui_parallel_wait_sec,
    dismiss_blocking_modals,
    open_settings_subroute,
    wait_for_state,
    warm_ui_route,
)

MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
SKILL_NAME = "hostile-json-notes"
SERVER_NAME = "hostile-json-server"
# The root object, ``mcpServers`` and the server entry enclose the value of ``capabilities``.
LEVELS_ABOVE_CAPABILITIES = 3

_DISMISS_MIGRATION_JS = """(() => {
  try {
    localStorage.setItem('myrm.proactive.dismissed.migration-discovery', 'forever');
  } catch (err) {
    return { ok: false, err: String(err) };
  }
  return { ok: true };
})()"""

# ``settings-layout`` mounts before the section does; ``data-active`` is the section itself.
_SETTINGS_READY_JS = """(() => {
  const shell = document.querySelector('[data-testid="settings-layout"]');
  const section = document.querySelector('[data-section="skills"][data-active]');
  return {
    ready:
      location.pathname.startsWith('/settings') &&
      !!shell &&
      shell.getAttribute('aria-busy') !== 'true' &&
      !!section,
    hasLayout: !!shell,
    hasSection: !!section,
  };
})()"""

# Polled until the dialog shows its file input; the open button is pressed only while no dialog is open.
_OPEN_IMPORT_DIALOG_JS = """(() => {
  const press = (el) => {
    const opts = { bubbles: true, cancelable: true, composed: true, button: 0, detail: 1, view: window };
    el.dispatchEvent(new PointerEvent('pointerdown', { ...opts, pointerId: 1, isPrimary: true }));
    el.dispatchEvent(new MouseEvent('mousedown', opts));
    el.dispatchEvent(new PointerEvent('pointerup', { ...opts, pointerId: 1, isPrimary: true }));
    el.dispatchEvent(new MouseEvent('mouseup', opts));
    el.dispatchEvent(new MouseEvent('click', opts));
  };
  const dialog = document.querySelector('[role="dialog"]');
  if (dialog && dialog.querySelector('input[type="file"]')) return { ready: true };
  const tab = Array.from(document.querySelectorAll('[role="tab"]')).find((el) =>
    /Installed|已安装/i.test((el.textContent || '').trim()),
  );
  if (tab && tab.getAttribute('aria-selected') !== 'true' && tab.getAttribute('data-state') !== 'active') press(tab);
  const button = Array.from(document.querySelectorAll('button[title]')).find((el) =>
    /Import Agent Plugin|导入 Agent 插件/.test(el.title),
  );
  if (button && !button.disabled && !dialog) press(button);
  return { ready: false, hasTab: !!tab, hasButton: !!button, hasDialog: !!dialog };
})()"""

_CHOOSE_ARCHIVE_JS = """(base64Zip) => {
  const input = document.querySelector('[role="dialog"] input[type="file"]');
  if (!input) return { ok: false, why: 'no file input in the dialog' };
  const bytes = Uint8Array.from(atob(base64Zip), (char) => char.charCodeAt(0));
  const transfer = new DataTransfer();
  transfer.items.add(new File([bytes], 'hostile-plugin.zip', { type: 'application/zip' }));
  input.files = transfer.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return { ok: true, chosen: input.files.length };
}"""

_PREVIEW_STATE_JS = f"""(() => {{
  const text = document.querySelector('[role="dialog"]')?.innerText || '';
  const notice = /MCP 服务器配置无效|MCP server settings are invalid/.test(text);
  const skill = text.includes({json.dumps(SKILL_NAME)});
  return {{
    ready: notice && skill,
    notice,
    skill,
    parseError: /解析失败|Parse Error/.test(text),
    snippet: text.slice(0, 600),
  }};
}})()"""


def _archive_base64() -> str:
    """A plugin whose mcp.json is valid but one level deeper than the loader accepts, next to a skill that is fine."""
    levels = MAX_NESTING + 1 - LEVELS_ABOVE_CAPABILITIES
    nested = '{"k":' * levels + "1" + "}" * levels
    server = f'{{"type": "stdio", "command": "tool", "capabilities": {nested}}}'
    files = {
        "plugin.json": json.dumps({"$schema": PLUGIN_SCHEMA, "name": "hostile-plugin"}),
        "mcp.json": f'{{"$schema": "{MCP_SCHEMA}", "mcpServers": {{"{SERVER_NAME}": {server}}}}}',
        f"skills/{SKILL_NAME}/SKILL.md": f"---\nname: {SKILL_NAME}\ndescription: Keeps notes tidy\n---\n\n# Notes\n",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path, text in files.items():
            archive.writestr(f"hostile-plugin/{path}", text)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_nested_mcp_config_is_reported_in_the_import_dialog_and_the_skill_stays() -> None:
    warm_ui_route("/settings")
    warm_ui_route("/settings/skills", timeout_sec=_warm_ui_parallel_wait_sec(180.0))
    with open_settings_subroute("/settings/skills", timeout_ms=120_000) as (client, page):
        client.evaluate(page, _DISMISS_MIGRATION_JS, timeout_sec=15.0)
        dismiss_blocking_modals(client, page)
        wait_for_state(client, page, _SETTINGS_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(120.0))
        wait_for_state(client, page, _OPEN_IMPORT_DIALOG_JS, timeout_sec=_warm_ui_parallel_wait_sec(60.0))

        chosen = client.evaluate(page, f"({_CHOOSE_ARCHIVE_JS})({json.dumps(_archive_base64())})", timeout_sec=30.0)
        assert chosen == {"ok": True, "chosen": 1}, chosen

        state = wait_for_state(client, page, _PREVIEW_STATE_JS, timeout_sec=_warm_ui_parallel_wait_sec(90.0))
        assert state["parseError"] is False, state
