"""Chrome E2E: the plugin import dialog reports an unreadable mcp.json (too deep, or an integer too long) and still offers the skills of the archive.

[INPUT]
- tests.support.chrome_plugin_import_e2e::(open_import_dialog, choose_archive, archive_with_nested_mcp_config, archive_with_overlong_integer_mcp_config, needs_digit_limit, TECHNICAL_DETAIL) (POS: reach the dialog, feed it the archive, tell what must not be on screen)
- tests.support.chrome_mcp_e2e::wait_for_state (POS: state polling)

[OUTPUT]
- test_an_unreadable_mcp_config_is_reported_in_the_import_dialog_and_the_skill_stays: fails when the dialog answers an archive whose
  mcp.json is one level deeper than ``MAX_NESTING``, or holds an integer of more digits than the interpreter converts, with a
  parse error, when the notice that no MCP server is imported is missing, when the skill of the same archive is not offered,
  or when the dialog shows the parser's English message.

[POS]
Browser-lane check of the path ``tests/integration`` covers without a UI: the archive is chosen in the file input of the dialog
opened from Settings > Skills, so the preview request, its diagnostics and the sentences the user reads are the product's own.
The notice is worded the same for every invalid mcp.json, so the archive's mcp.json is valid in every respect but its depth or
its integer: the same document one level shallower, or with a short integer, lists its server instead. The preview persists the archive as an import session under a
random id, which the server sweeps after 24 hours; nothing is installed, so that session is the node's only write.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from tests.support.chrome_mcp_e2e import _warm_ui_parallel_wait_sec, wait_for_state
from tests.support.chrome_plugin_import_e2e import (
    TECHNICAL_DETAIL,
    archive_with_nested_mcp_config,
    archive_with_overlong_integer_mcp_config,
    choose_archive,
    needs_digit_limit,
    open_import_dialog,
)

SKILL_NAME = "hostile-json-notes"

_PREVIEW_STATE_JS = f"""(() => {{
  const text = document.querySelector('[role="dialog"]')?.innerText || '';
  const notice = /MCP 服务器配置无效|MCP server settings are invalid/.test(text);
  const skill = text.includes({json.dumps(SKILL_NAME)});
  return {{
    ready: notice && skill,
    notice,
    skill,
    parseError: /解析失败|Parse Error/.test(text),
    text,
  }};
}})()"""


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.parametrize(
    "make_archive",
    [
        pytest.param(archive_with_nested_mcp_config, id="nested"),
        pytest.param(archive_with_overlong_integer_mcp_config, id="overlong-integer", marks=needs_digit_limit),
    ],
)
def test_an_unreadable_mcp_config_is_reported_in_the_import_dialog_and_the_skill_stays(
    make_archive: Callable[[str], dict[str, str]],
) -> None:
    with open_import_dialog() as (client, page):
        choose_archive(client, page, make_archive(SKILL_NAME))

        state = wait_for_state(client, page, _PREVIEW_STATE_JS, timeout_sec=_warm_ui_parallel_wait_sec(90.0))
        assert state["parseError"] is False, state
        assert not TECHNICAL_DETAIL.search(str(state["text"])), state
