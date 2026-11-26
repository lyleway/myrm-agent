"""JSON loading for documents from outside the process, whose strings must stay UTF-8 encodable.

``json.loads`` turns a surrogate escape (``"\\ud800"``) into a lone surrogate, which no JSON response, HTTP body or SQLite
bind can encode, so one such value in a plugin.json or mcp.json fails every request that echoes it. The loader here
rewrites those escapes in the text before the parse, so every string and key at any depth comes out encodable without a
walk over the result (which would have to follow nesting as deep as the parser accepts).

[INPUT]
- utils.text_sanitizer::encodable_text (POS: repairs a text that only has to become UTF-8 encodable, replacing each lone surrogate with ``U+FFFD``)

[OUTPUT]
- load_clean_json: ``json.loads`` of one document whose every string, key or value at any depth is UTF-8 encodable

[POS]
Parse step for the plugin.json and mcp.json of a plugin package that comes from an archive the user uploaded or a market supplied.
"""

from __future__ import annotations

import json
import re

from myrm_agent_harness.utils.text_sanitizer import encodable_text

__all__ = ["load_clean_json"]

# ``json.dumps`` writes CJK and Korean text as ``\u`` escapes that are no surrogates, and the rewrite below calls back for every
# escape it passes, so only a text that holds something the shape of a surrogate escape goes through it.
_SURROGATE_ESCAPE = re.compile(r"\\u[dD][89a-fA-F]")

# Escapes are matched left to right and a backslash is consumed together with the character after it, so ``\\ud800``
# (an escaped backslash, then the text ``ud800``) is never read as an escape. A high surrogate escape followed by a low one
# spells one character and is kept; any other surrogate escape stands alone.
_ESCAPE = re.compile(
    r"\\u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}"
    r"|(?P<lone>\\u[dD][89a-fA-F][0-9a-fA-F]{2})"
    r"|\\."
)
# Same length as the escape it replaces, so a syntax error reports the position it would report for the original text.
_REPLACEMENT_ESCAPE = "\\ufffd"


def _repair_escape(match: re.Match[str]) -> str:
    return _REPLACEMENT_ESCAPE if match.group("lone") else match.group()


def load_clean_json(text: str) -> object:
    """Parse one JSON document like ``json.loads`` (same types, same errors), every string made encodable.

    A lone surrogate escape becomes ``U+FFFD`` and nothing else changes; an escaped pair is the one character it spells.
    Two keys that differ only in their lone surrogates become one key, the later value winning as for any repeated key.
    """
    text = encodable_text(text)
    if _SURROGATE_ESCAPE.search(text):
        text = _ESCAPE.sub(_repair_escape, text)
    return json.loads(text)
