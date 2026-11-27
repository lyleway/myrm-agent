"""Page script of the Chrome E2E tests that open a conversation by its web hand-off link.

[INPUT]
- none (builds JavaScript source)

[OUTPUT]
- conversation_rows_hold_js(): resolves once the message rows of the conversation view hold every given text

[POS]
Shared by the landing and continue hand-off Chrome E2E tests. Only ``[data-message-id]`` rows count: a page that did
not open the chat can still echo it elsewhere (the catch-up inbox card shows the question and a reply preview), so a
whole-page text match passes on a wrong route.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

ROWS_WAIT_MS = 90_000


def conversation_rows_hold_js(needles: Sequence[str], *, wait_ms: int = ROWS_WAIT_MS) -> str:
    """Script that resolves ``{ok: true, path, rows}`` once every needle is inside some message row, else ``{ok: false, ...}``."""
    return f"""(async () => {{
  const needles = {json.dumps(list(needles))};
  const deadline = Date.now() + {wait_ms};
  const rowTexts = () => Array.from(document.querySelectorAll('[data-message-id]')).map((row) => row.innerText || '');
  while (Date.now() < deadline) {{
    const rows = rowTexts();
    if (needles.every((needle) => rows.some((text) => text.includes(needle)))) {{
      return {{ ok: true, path: location.pathname, rows: rows.length }};
    }}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }}
  const page = document.body ? document.body.innerText : '';
  return {{ ok: false, path: location.pathname, rows: rowTexts().map((text) => text.slice(0, 120)), page: page.slice(0, 800) }};
}})()"""
