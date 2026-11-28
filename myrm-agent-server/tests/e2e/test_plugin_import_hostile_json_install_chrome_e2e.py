"""Chrome E2E: the skill of a plugin whose mcp.json is refused is imported from the dialog and listed as installed.

[INPUT]
- tests.support.chrome_plugin_import_e2e::(open_import_dialog, choose_archive, archive_with_nested_mcp_config, TECHNICAL_DETAIL) (POS: reach the dialog, feed it the archive, tell what must not be on screen)
- tests.support.chrome_mcp_e2e::(wait_for_state, http_json, get_e2e_api_url) (POS: state polling, API reads and the skill cleanup)

[OUTPUT]
- test_the_skill_of_a_plugin_with_a_refused_mcp_config_is_imported_from_the_dialog: fails when the dialog does not let the user
  import the skill of an archive whose mcp.json is one level deeper than ``MAX_NESTING``, when the result does not say one skill
  and no MCP server were imported, when the skill is not installed on the server or not in the Installed list afterwards, when
  the dialog shows the parser's English message, or when the skill is still installed after the test removed it.

[POS]
The notice for a refused mcp.json promises that the skills can still be imported. This node keeps that promise: it ticks the
trust box, confirms, reads the result the user reads, and finds the skill in the Installed list the dialog refreshes. The skill
is installed in the global skill store, which no namespace can scope, so the node runs on a private backend; it carries a
random name and is uninstalled whatever the outcome.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.support.chrome_mcp_e2e import _warm_ui_parallel_wait_sec, get_e2e_api_url, http_json, wait_for_state
from tests.support.chrome_plugin_import_e2e import (
    TECHNICAL_DETAIL,
    archive_with_nested_mcp_config,
    choose_archive,
    open_import_dialog,
)

_PREVIEW_STATE_JS = r"""(name) => {
  const dialog = document.querySelector('[role="dialog"]');
  const text = dialog?.innerText || '';
  const notice = /MCP 服务器配置无效|MCP server settings are invalid/.test(text);
  const skill = text.includes(name);
  return {
    ready: notice && skill && !!dialog.querySelector('[data-testid="trusted-source-checkbox"]'),
    notice,
    skill,
    parseError: /解析失败|Parse Error/.test(text),
    text,
  };
}"""

# One step toward the click per poll: the confirm button enables only after React has seen the ticked box.
_CONFIRM_JS = r"""(() => {
  const dialog = document.querySelector('[role="dialog"]');
  const trust = dialog?.querySelector('[data-testid="trusted-source-checkbox"]');
  const confirm = Array.from(dialog?.querySelectorAll('button') || []).find((el) =>
    /确认导入|Confirm Import/.test(el.innerText || ''),
  );
  if (!trust || !confirm) return { ready: false, hasTrust: !!trust, hasConfirm: !!confirm };
  if (!trust.checked) {
    trust.click();
    return { ready: false, ticked: true };
  }
  if (confirm.disabled) return { ready: false, confirmDisabled: true };
  confirm.click();
  return { ready: true };
})()"""

_RESULT_STATE_JS = r"""(() => {
  const text = document.querySelector('[role="dialog"]')?.innerText || '';
  const counts =
    /已导入 (\d+) 个智能体、(\d+) 个技能、(\d+) 个 MCP 服务器/.exec(text) ||
    /Imported (\d+) agents, (\d+) skills and (\d+) MCP servers/.exec(text);
  return { ready: !!counts, counts: counts ? counts.slice(1, 4).map(Number) : null, text };
})()"""

_CLOSE_DIALOG_JS = r"""(() => {
  const dialog = document.querySelector('[role="dialog"]');
  if (!dialog) return { ready: true };
  const done = Array.from(dialog.querySelectorAll('button')).find((el) => /^(完成|Done)$/.test((el.innerText || '').trim()));
  if (done) done.click();
  return { ready: false, hasDone: !!done };
})()"""

# The list is filtered to the skill, as a user does, so that the card is on screen however long the list is.
_INSTALLED_CARD_JS = r"""(name) => {
  const input = Array.from(document.querySelectorAll('input')).find((el) =>
    /搜索已安装技能|Search installed skills/.test(el.placeholder || ''),
  );
  if (input && input.value !== name) {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, name);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
  const text = document.querySelector('[data-section="skills"][data-active]')?.innerText || '';
  return { ready: !!input && input.value === name && text.includes(name), hasInput: !!input, snippet: text.slice(0, 400) };
}"""


def _call(arrow_function: str, name: str) -> str:
    return f"({arrow_function})({json.dumps(name)})"


def _installed_skill_ids(api_url: str, name: str) -> list[str]:
    body = http_json("GET", f"{api_url}/api/v1/skills/")
    assert isinstance(body, dict), body
    skills = body.get("skills")
    assert isinstance(skills, list), body
    return [str(skill["id"]) for skill in skills if isinstance(skill, dict) and skill.get("name") == name]


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_the_skill_of_a_plugin_with_a_refused_mcp_config_is_imported_from_the_dialog() -> None:
    api_url = get_e2e_api_url()
    name = f"hostile-json-notes-{uuid.uuid4().hex[:8]}"
    assert _installed_skill_ids(api_url, name) == []

    try:
        with open_import_dialog() as (client, page):
            choose_archive(client, page, archive_with_nested_mcp_config(name))
            preview = wait_for_state(client, page, _call(_PREVIEW_STATE_JS, name), timeout_sec=_warm_ui_parallel_wait_sec(90.0))
            assert preview["parseError"] is False, preview
            assert not TECHNICAL_DETAIL.search(str(preview["text"])), preview

            wait_for_state(client, page, _CONFIRM_JS, timeout_sec=_warm_ui_parallel_wait_sec(60.0))
            result = wait_for_state(client, page, _RESULT_STATE_JS, timeout_sec=_warm_ui_parallel_wait_sec(90.0))
            assert result["counts"] == [0, 1, 0], result
            assert not TECHNICAL_DETAIL.search(str(result["text"])), result
            assert len(_installed_skill_ids(api_url, name)) == 1

            wait_for_state(client, page, _CLOSE_DIALOG_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))
            wait_for_state(client, page, _call(_INSTALLED_CARD_JS, name), timeout_sec=_warm_ui_parallel_wait_sec(60.0))
    finally:
        for skill_id in _installed_skill_ids(api_url, name):
            http_json("POST", f"{api_url}/api/v1/skills/discovery/uninstall", {"skill_id": skill_id, "force": True})
    assert _installed_skill_ids(api_url, name) == []
