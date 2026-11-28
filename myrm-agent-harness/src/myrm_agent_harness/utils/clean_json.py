"""JSON loading for documents from outside the process, whose strings must stay UTF-8 encodable and whose nesting is bounded.

``json.loads`` turns a surrogate escape (``"\\ud800"``) into a lone surrogate, which no JSON response, HTTP body or SQLite
bind can encode, so one such value in a plugin.json or mcp.json fails every request that echoes it. The loader here
rewrites those escapes in the text before the parse, so every string and key at any depth comes out encodable without a
walk over the result.

``json.loads`` also accepts nesting up to its recursion limit, which lies far beyond what the code that walks a decoded
document can follow: pickling a staged import session, for one, fails at a depth the parser still accepts. The loader
therefore refuses a document nested deeper than ``MAX_NESTING`` levels before the parse, which also keeps the parser itself
far from its recursion limit.

An integer with more digits than the interpreter converts (``sys.get_int_max_str_digits()``) makes ``json.loads`` raise a bare
``ValueError``, no ``json.JSONDecodeError``, so a caller that catches the latter lets it escape, and its message tells the
reader to raise an interpreter setting. The loader raises a ``json.JSONDecodeError`` at the number instead, so every refusal
of a document has one type.

[INPUT]
- utils.text_sanitizer::encodable_text (POS: repairs a text that only has to become UTF-8 encodable, replacing each lone surrogate with ``U+FFFD``)

[OUTPUT]
- MAX_NESTING: the deepest nesting of arrays and objects a loaded document may have
- load_clean_json: ``json.loads`` of one document whose every string, key or value at any depth is UTF-8 encodable, whose nesting is at most ``MAX_NESTING`` levels and whose refusal is always a ``json.JSONDecodeError``

[POS]
Parse step for the plugin.json and mcp.json of a plugin package that comes from an archive the user uploaded or a market supplied.
"""

from __future__ import annotations

import json
import re
import sys

from myrm_agent_harness.utils.text_sanitizer import encodable_text

__all__ = ["MAX_NESTING", "load_clean_json"]

# Real manifests stay within a handful of levels; the limit leaves room for nested extension data and sits far below the
# depth at which a consumer that recurses over the result runs out of stack.
MAX_NESTING = 64

# A string literal, to be consumed whole by the scans below, so that nothing inside it counts as a bracket or a digit; an
# unterminated one runs to the end of the text, as the parser's own error does. A backslash takes the character after it, a line
# break included (hence DOTALL in every pattern that holds this one), and the repeat is possessive: once its opening quote has
# matched, a literal can neither fail nor give characters back, so every character is read once and no backtracking state is
# kept, however many quotes, backslashes and line breaks an input piles up.
_STRING_LITERAL = r'"(?:[^"\\]+|\\.?)*+(?:"|\Z)'

# One token per string literal, opening bracket (group 1) or closing bracket (group 2); all other text is skipped.
_STRING_OR_BRACKET = re.compile(_STRING_LITERAL + r"|([\[{])|([\]}])", re.DOTALL)

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


def _first_overlong_digit_run(text: str, limit: int) -> int | None:
    """Offset of the first run of more than ``limit`` digits outside every string literal, ``None`` if there is none.

    One match from the start of the text consumes string literals, other characters and the digit runs that fit the limit, all
    of it possessively, so the text is read once whatever it piles up; a search would start that pass again at every offset
    when the text holds no such run. There is none to find when the interpreter converts integers of any length (``limit`` 0).
    """
    if not limit:
        return None
    pattern = rf'(?:{_STRING_LITERAL}|[^"0-9]++|[0-9]{{1,{limit}}}+(?![0-9]))*+([0-9]{{{limit + 1},}})'
    match = re.match(pattern, text, re.DOTALL)
    return match.start(1) if match else None


def load_clean_json(text: str) -> object:
    """Parse one JSON document like ``json.loads`` (same types, same syntax errors), every string made encodable.

    A lone surrogate escape becomes ``U+FFFD`` and nothing else changes; an escaped pair is the one character it spells.
    Two keys that differ only in their lone surrogates become one key, the later value winning as for any repeated key.
    A document nested deeper than ``MAX_NESTING`` levels raises ``json.JSONDecodeError`` at the bracket that opens the first
    level over the limit, and one with a number of more digits than ``sys.get_int_max_str_digits()`` raises it at the first
    digit of that number.
    """
    text = encodable_text(text)
    _reject_deep_nesting(text)
    if _SURROGATE_ESCAPE.search(text):
        text = _ESCAPE.sub(_repair_escape, text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise
    except ValueError:
        # A bare ValueError is the parser's answer to an integer of more digits than ``int`` converts; any other one stays as is.
        limit = sys.get_int_max_str_digits()
        position = _first_overlong_digit_run(text, limit)
        if position is None:
            raise
        raise json.JSONDecodeError(f"Number has more than {limit} digits", text, position) from None
