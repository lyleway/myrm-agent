"""JSON loading for documents from outside the process, whose strings must stay UTF-8 encodable and whose nesting is bounded.

``json.loads`` turns a surrogate escape (``"\\ud800"``) into a lone surrogate, which no JSON response, HTTP body or SQLite
bind can encode, so one such value in a plugin.json or mcp.json fails every request that echoes it. The loader here
rewrites those escapes in the text before the parse, so every string and key at any depth comes out encodable without a
walk over the result.

``json.loads`` also accepts nesting up to its recursion limit, which lies far beyond what the code that walks a decoded
document can follow: pickling a staged import session, for one, fails at a depth the parser still accepts. The loader
therefore refuses a document nested deeper than ``MAX_NESTING`` levels before the parse, which also keeps the parser itself
far from its recursion limit.

[INPUT]
- utils.text_sanitizer::encodable_text (POS: repairs a text that only has to become UTF-8 encodable, replacing each lone surrogate with ``U+FFFD``)

[OUTPUT]
- MAX_NESTING: the deepest nesting of arrays and objects a loaded document may have
- load_clean_json: ``json.loads`` of one document whose every string, key or value at any depth is UTF-8 encodable and whose nesting is at most ``MAX_NESTING`` levels

[POS]
Parse step for the plugin.json and mcp.json of a plugin package that comes from an archive the user uploaded or a market supplied.
"""

from __future__ import annotations

import json
import re

from myrm_agent_harness.utils.text_sanitizer import encodable_text

__all__ = ["MAX_NESTING", "load_clean_json"]

# Real manifests stay within a handful of levels; the limit leaves room for nested extension data and sits far below the
# depth at which a consumer that recurses over the result runs out of stack.
MAX_NESTING = 64

# One token per string literal, opening bracket (group 1) or closing bracket (group 2); all other text is skipped. A literal is
# consumed whole, so a bracket inside a string is no level, and an unterminated one runs to the end of the text, as the
# parser's own error does: every character is scanned once, however many quotes and backslashes an input piles up.
_STRING_OR_BRACKET = re.compile(r'"[^"\\]*(?:\\(?:.|\Z)[^"\\]*)*(?:"|\Z)|([\[{])|([\]}])')

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


def _reject_deep_nesting(text: str) -> None:
    """Raise ``json.JSONDecodeError`` at the bracket that opens the first level over ``MAX_NESTING``.

    The depth is that of the brackets read so far, closed or not, so no text can send the parser deeper than the limit,
    whatever else is wrong with it.
    """
    # A level needs an opener of its own, so fewer openers than the limit cannot nest past it. The count includes brackets
    # inside strings, which can only overstate it; this spares the scan to every document that is small or mostly text.
    if text.count("[") + text.count("{") <= MAX_NESTING:
        return
    depth = 0
    for token in _STRING_OR_BRACKET.finditer(text):
        if token.lastindex == 1:
            depth += 1
            if depth > MAX_NESTING:
                raise json.JSONDecodeError(f"Nesting is deeper than {MAX_NESTING} levels", text, token.start())
        elif token.lastindex == 2:
            depth -= 1


def load_clean_json(text: str) -> object:
    """Parse one JSON document like ``json.loads`` (same types, same errors), every string made encodable.

    A lone surrogate escape becomes ``U+FFFD`` and nothing else changes; an escaped pair is the one character it spells.
    Two keys that differ only in their lone surrogates become one key, the later value winning as for any repeated key.
    A document nested deeper than ``MAX_NESTING`` levels raises ``json.JSONDecodeError`` at the bracket that opens the first
    level over the limit.
    """
    text = encodable_text(text)
    _reject_deep_nesting(text)
    if _SURROGATE_ESCAPE.search(text):
        text = _ESCAPE.sub(_repair_escape, text)
    return json.loads(text)
