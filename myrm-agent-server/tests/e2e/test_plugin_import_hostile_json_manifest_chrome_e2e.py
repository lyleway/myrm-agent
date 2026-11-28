"""Chrome E2E: the plugin import dialog explains a plugin.json that is unreadable (too deep, or an integer too long) and offers nothing to import.

[INPUT]
- tests.support.chrome_plugin_import_e2e::(open_import_dialog, choose_archive, archive_with_nested_manifest, archive_with_overlong_integer_manifest, needs_digit_limit, TECHNICAL_DETAIL) (POS: reach the dialog, feed it the archive, tell what must not be on screen)
- tests.support.chrome_mcp_e2e::wait_for_state (POS: state polling)

[OUTPUT]
- test_an_unreadable_manifest_is_explained_in_the_import_dialog_and_nothing_is_offered: fails when the dialog answers an archive
  whose plugin.json is one level deeper than ``MAX_NESTING``, or holds an integer of more digits than the interpreter converts,
  with a parse error instead of a preview, when the notice that the archive is not a supported plugin package is missing, when
  the skill of the archive is offered, when the confirm button is enabled, or when the dialog shows the parser's English message.

[POS]
A plugin.json the loader refuses is fatal to the whole plugin, so the preview request is answered with a package that has no
name and no components; the dialog has to say so instead of reporting a failed upload, and must not let the user confirm an
empty import. The notice is worded the same for every unreadable plugin.json, so the archive's plugin.json is valid in every
respect but its depth or its integer: the same document one level shallower, or with a short integer, shows the plugin and
offers its skill. The preview persists the
archive as an import session under a random id, which the server sweeps after 24 hours; nothing is installed, so that session
is the node's only write.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from tests.support.chrome_mcp_e2e import _warm_ui_parallel_wait_sec, wait_for_state
from tests.support.chrome_plugin_import_e2e import (
    TECHNICAL_DETAIL,
    archive_with_nested_manifest,
    archive_with_overlong_integer_manifest,
    choose_archive,
    needs_digit_limit,
    open_import_dialog,
)

SKILL_NAME = "hostile-json-notes"

_PREVIEW_STATE_JS = f"""(() => {{
  const dialog = document.querySelector('[role="dialog"]');
  const text = dialog?.innerText || '';
  const confirm = Array.from(dialog?.querySelectorAll('button') || []).find((el) =>
    /确认导入|Confirm Import/.test(el.innerText || ''),
  );
  const notice = /这不是受支持的插件包，无法导入|This is not a supported plugin package/.test(text);
  return {{
    ready: notice && !!confirm,
    notice,
    emptyState: /该插件没有可导入的组件|No importable components in this plugin/.test(text),
    skill: text.includes({json.dumps(SKILL_NAME)}),
    parseError: /解析失败|Parse Error/.test(text),
    confirmDisabled: confirm ? confirm.disabled : null,
    text,
  }};
}})()"""


@pytest.mark.chrome_e2e(execution_mode="SHARED", access_scope="NAMESPACE_WRITE", workload="STANDARD")
@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.parametrize(
    "make_archive",
    [
        pytest.param(archive_with_nested_manifest, id="nested"),
        pytest.param(archive_with_overlong_integer_manifest, id="overlong-integer", marks=needs_digit_limit),
    ],
)
def test_an_unreadable_manifest_is_explained_in_the_import_dialog_and_nothing_is_offered(
    make_archive: Callable[[str], dict[str, str]],
) -> None:
    with open_import_dialog() as (client, page):
        choose_archive(client, page, make_archive(SKILL_NAME))

        state = wait_for_state(client, page, _PREVIEW_STATE_JS, timeout_sec=_warm_ui_parallel_wait_sec(90.0))
        shown = (state["parseError"], state["emptyState"], state["skill"], state["confirmDisabled"])
        assert shown == (False, True, False, True), state
        assert not TECHNICAL_DETAIL.search(str(state["text"])), state
