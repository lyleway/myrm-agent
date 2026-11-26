"""A page probe for words the page shows for a moment: a toast or an inline verdict that clears itself.

[INPUT]
- (standard library only)

[OUTPUT]
- page_text_matches_js(): an in-page expression that is ready once the page text has matched a regular expression

[POS]
Server pytest-only helper; pass the expression to ``tests.support.chrome_mcp_e2e.wait_for_state``.
"""

from __future__ import annotations

import json


def page_text_matches_js(pattern: str) -> str:
    """Probe whether the page text has matched the case-insensitive, dot-all regex ``pattern``; ``text`` is the match.

    The first probe starts watching the page, and the first match is kept: a toast that is gone before the next poll
    still counts. The match is kept until the page reloads, so use one pattern for one wait.
    """
    return f"""(() => {{
  const pattern = {json.dumps(pattern)};
  const regex = new RegExp(pattern, 'is');
  const latches = (window.__e2ePageTextLatches = window.__e2ePageTextLatches || {{}});
  const latch = (latches[pattern] = latches[pattern] || {{ text: null }});
  const look = () => {{
    const match = regex.exec(document.body?.innerText || '');
    if (match && !latch.text) latch.text = match[0].slice(0, 400);
  }};
  look();
  if (!latch.observer) {{
    latch.observer = new MutationObserver(look);
    latch.observer.observe(document.body, {{ childList: true, subtree: true, characterData: true }});
  }}
  return {{ ready: !!latch.text, text: latch.text || (document.body?.innerText || '').slice(0, 400) }};
}})()"""
