"""Chrome helpers shared by the plugin import dialog nodes: reach the dialog, hand it an archive, build hostile archives.

[INPUT]
- tests.support.chrome_mcp_e2e::(open_settings_subroute, wait_for_state, dismiss_blocking_modals, warm_ui_route) (POS: owned Chrome page + state polling)
- tests.support.redaction_review_e2e::DISMISS_MIGRATION_JS (POS: stops the migration banner from covering the page)
- myrm_agent_harness.utils.clean_json::MAX_NESTING (POS: the deepest nesting of a document the loader accepts)

[OUTPUT]
- PLUGIN_NAME, SERVER_NAME: names inside the archives
- TECHNICAL_DETAIL: what the backend's English diagnostic of an unreadable document says, which must never reach the dialog
- archive_with_nested_mcp_config / archive_with_nested_manifest: a plugin whose one document is valid in every respect but its depth
- open_import_dialog: Settings > Skills with the import dialog open on its file input
- choose_archive: puts a ZIP of the given files in the dialog's file input, as the file chooser does

[POS]
The three nodes of ``test_plugin_import_hostile_json_*`` differ in what they read from the dialog, not in how they reach it
or what they feed it. The notice a user reads is worded the same for every unreadable mcp.json (and for every unreadable
plugin.json), so each archive is valid except for its depth: only then does the notice prove that the depth was the reason.
A document takes arbitrary JSON in one place only, the value of a ``capabilities`` field of an mcp.json server and of an
``extensions`` namespace of a plugin.json; the nested value goes there and the enclosing levels are counted.
"""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from myrm_agent_harness.utils.clean_json import MAX_NESTING

from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    _warm_ui_parallel_wait_sec,
    dismiss_blocking_modals,
    open_settings_subroute,
    wait_for_state,
    warm_ui_route,
)
from tests.support.redaction_review_e2e import DISMISS_MIGRATION_JS

MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
PLUGIN_NAME = "hostile-plugin"
SERVER_NAME = "hostile-json-server"
_ARCHIVE_NAME = f"{PLUGIN_NAME}.zip"
# The preview carries the parser's English message of an unreadable document; the client logs it to the console and
# shows a sentence of its own, so none of it may be on screen.
TECHNICAL_DETAIL = re.compile(r"not valid JSON|Nesting is deeper|_invalid_json|column \d+", re.IGNORECASE)
# The root object, ``mcpServers`` and the server entry enclose the value of ``capabilities``.
_LEVELS_ABOVE_CAPABILITIES = 3
# The root object and ``extensions`` enclose the value of a namespace.
_LEVELS_ABOVE_EXTENSION = 2

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

_CHOOSE_ARCHIVE_JS = """(base64Zip, fileName) => {
  const input = document.querySelector('[role="dialog"] input[type="file"]');
  if (!input) return { ok: false, why: 'no file input in the dialog' };
  const bytes = Uint8Array.from(atob(base64Zip), (char) => char.charCodeAt(0));
  const transfer = new DataTransfer();
  transfer.items.add(new File([bytes], fileName, { type: 'application/zip' }));
  input.files = transfer.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return { ok: true, chosen: input.files.length };
}"""


def _nested(levels: int) -> str:
    """An object nested ``levels`` deep around a number."""
    return '{"k":' * levels + "1" + "}" * levels


def _skill_files(skill_name: str) -> dict[str, str]:
    skill_md = f"---\nname: {skill_name}\ndescription: Keeps notes tidy\n---\n\n# Notes\n"
    return {f"skills/{skill_name}/SKILL.md": skill_md}


def archive_with_nested_mcp_config(skill_name: str, *, depth: int = MAX_NESTING + 1) -> dict[str, str]:
    """A plugin whose mcp.json is ``depth`` levels deep and valid in every other respect, next to a skill that is fine."""
    nested = _nested(depth - _LEVELS_ABOVE_CAPABILITIES)
    server = f'{{"type": "stdio", "command": "tool", "capabilities": {nested}}}'
    return {
        "plugin.json": json.dumps({"$schema": PLUGIN_SCHEMA, "name": PLUGIN_NAME}),
        "mcp.json": f'{{"$schema": "{MCP_SCHEMA}", "mcpServers": {{"{SERVER_NAME}": {server}}}}}',
        **_skill_files(skill_name),
    }


def archive_with_nested_manifest(skill_name: str, *, depth: int = MAX_NESTING + 1) -> dict[str, str]:
    """A plugin whose plugin.json is ``depth`` levels deep and valid in every other respect, next to a skill that is fine."""
    nested = _nested(depth - _LEVELS_ABOVE_EXTENSION)
    manifest = f'{{"$schema": "{PLUGIN_SCHEMA}", "name": "{PLUGIN_NAME}", "extensions": {{"ai.example": {nested}}}}}'
    return {"plugin.json": manifest, **_skill_files(skill_name)}


def _zip_base64(files: Mapping[str, str]) -> str:
    buffer = io.BytesIO()
    # Stored, not deflated: repeated text compresses so well that the archive guard would take it for a zip bomb.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path, text in files.items():
            archive.writestr(f"{PLUGIN_NAME}/{path}", text)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@contextmanager
def open_import_dialog() -> Iterator[tuple[ChromeMcpClient, McpPage]]:
    """Settings > Skills with the plugin import dialog open on its file input."""
    warm_ui_route("/settings")
    # The skills route compiles slowly on a cold dev server; hydrating it unwarmed times out under load.
    warm_ui_route("/settings/skills", timeout_sec=_warm_ui_parallel_wait_sec(180.0))
    with open_settings_subroute("/settings/skills", timeout_ms=120_000) as (client, page):
        client.evaluate(page, DISMISS_MIGRATION_JS, timeout_sec=15.0)
        dismiss_blocking_modals(client, page)
        wait_for_state(client, page, _SETTINGS_READY_JS, timeout_sec=_warm_ui_parallel_wait_sec(120.0))
        wait_for_state(client, page, _OPEN_IMPORT_DIALOG_JS, timeout_sec=_warm_ui_parallel_wait_sec(60.0))
        yield client, page


def choose_archive(client: ChromeMcpClient, page: McpPage, files: Mapping[str, str]) -> None:
    """Put a ZIP of ``files`` in the file input of the open dialog, which is what the file chooser does."""
    call = f"({_CHOOSE_ARCHIVE_JS})({json.dumps(_zip_base64(files))}, {json.dumps(_ARCHIVE_NAME)})"
    chosen = client.evaluate(page, call, timeout_sec=30.0)
    assert chosen == {"ok": True, "chosen": 1}, chosen
