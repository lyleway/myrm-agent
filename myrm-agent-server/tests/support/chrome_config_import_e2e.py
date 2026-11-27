"""Settings > Developer > Import/Export: loading a configuration backup file, as the user does it in the WebUI.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: CDP page helpers)

[OUTPUT]
- CONFIG_IMPORT_ROUTE: the settings route of the import/export section
- import_config_backup(): choose a backup file in the section and confirm the import in the preview dialog
- IMPORT_BLOCKED / IMPORT_SUCCEEDED: the words of the result line under the import buttons

[POS]
Server pytest-only helper for the configuration-backup Chrome E2E tests. The file reaches the section the way a picked
file does (its file input gets the file and fires ``change``); the preview dialog and its confirm button are the real ones.
"""

from __future__ import annotations

import json

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, wait_for_state

CONFIG_IMPORT_ROUTE = "/settings/developer?sub=importExport"

IMPORT_BLOCKED = r"(Import blocked: an MCP configuration failed the security scan|导入已阻止：某个 MCP 配置未通过安全扫描)"
IMPORT_SUCCEEDED = r"(Configuration imported successfully|配置导入成功)"

_CONFIRM_IMPORT_JS = """(() => {
  const confirm = Array.from(document.querySelectorAll('button')).find((b) =>
    /Confirm Import|确认导入/.test((b.textContent || '').trim())
  );
  if (!confirm || confirm.disabled) return { ready: false };
  confirm.click();
  return { ready: true };
})()"""


def _choose_file_js(file_name: str, text: str) -> str:
    # The section mounts after the settings shell, so a missing input only means "not yet".
    return f"""(() => {{
  const input = document.querySelector('input[type="file"][accept=".json"]');
  if (!input) return {{ ready: false, err: 'file-input-not-found' }};
  const transfer = new DataTransfer();
  transfer.items.add(new File([{json.dumps(text)}], {json.dumps(file_name)}, {{ type: 'application/json' }}));
  input.files = transfer.files;
  input.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return {{ ready: true }};
}})()"""


def import_config_backup(client: ChromeMcpClient, page: McpPage, backup: dict[str, object]) -> None:
    """Choose ``backup`` as the backup file, then press the preview dialog's confirm button once it is enabled."""
    wait_for_state(client, page, _choose_file_js("myrm-config-backup.json", json.dumps(backup)), timeout_sec=45.0)
    wait_for_state(client, page, _CONFIRM_IMPORT_JS, timeout_sec=30.0)
