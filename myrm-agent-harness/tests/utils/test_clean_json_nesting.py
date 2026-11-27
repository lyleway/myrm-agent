"""Tests for the nesting limit of load_clean_json: a document nested deeper than ``MAX_NESTING`` levels is refused before the parse.

[INPUT]
- utils.clean_json::load_clean_json, MAX_NESTING (POS: parse step for the plugin.json and mcp.json of a plugin archive)
- json (POS: the parser whose ``loads`` the loader must agree with within the limit)

[OUTPUT]
- Fails when a document within the limit is refused or changes, when one over it is accepted or raises anything but a
  ``json.JSONDecodeError`` at the bracket that opens the first level over the limit, when a bracket inside a string counts as
  a level or a string hides one, when text the parser would reject for its syntax is refused for its nesting, when the scan of
  text that piles up quotes, backslashes and line breaks costs more than a fixed CPU budget, or when a long string literal
  makes it hold memory beyond a small multiple of the text.

[POS]
The parser recurses once per level and accepts about ten thousand, while the code that pickles a staged import session or walks
a decoded document gives up at a few thousand; a hostile plugin.json or mcp.json would otherwise fail the import preview.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import re
import time
import tracemalloc
from collections.abc import Callable

import pytest

from myrm_agent_harness.utils.clean_json import MAX_NESTING, load_clean_json

PARSER_RECURSION_DEPTH = 20_000  # past the depth at which json.loads itself raises RecursionError

# The characters a scan of string literals reacts to: a quote opens or closes one, a backslash escapes the character after it
# (a line break too), a closing bracket is no level inside one. A text made of an opener, a long run of one pump and a tail,
# all drawn from them, is what sends a backtracking scan over the same characters again and again.
SCAN_SYMBOLS = ('"', "\\", "\n", "]")
PUMP_REPEATS = 3_000
SCAN_CPU_BUDGET = 0.025  # seconds: far above reading each character once, far below rescanning from every quote
ONE_MIB = 1 << 20


def _array(depth: int) -> str:
    return "[" * depth + "]" * depth


def _object(depth: int) -> str:
    return '{"k":' * depth + "1" + "}" * depth


def _mixed(depth: int) -> str:
    openers = ["[" if level % 2 else '{"k":' for level in range(depth)]
    closers = ["]" if level % 2 else "}" for level in reversed(range(depth))]
    return "".join(openers) + "1" + "".join(closers)


STYLES: dict[str, Callable[[int], str]] = {"arrays": _array, "objects": _object, "mixed": _mixed}


def _refusal(document: str) -> json.JSONDecodeError:
    with pytest.raises(json.JSONDecodeError) as caught:
        load_clean_json(document)
    return caught.value


@pytest.mark.parametrize("style", STYLES)
def test_a_document_nested_to_the_limit_loads_as_json_loads_does(style: str) -> None:
    document = STYLES[style](MAX_NESTING)

    assert load_clean_json(document) == json.loads(document)


@pytest.mark.parametrize("style", STYLES)
def test_one_level_over_the_limit_is_refused_at_the_bracket_that_opens_it(style: str) -> None:
    document = STYLES[style](MAX_NESTING + 1)
    openers = [match.start() for match in re.finditer(r"[\[{]", document)]

    error = _refusal(document)

    assert error.msg == f"Nesting is deeper than {MAX_NESTING} levels"
    assert error.pos == openers[MAX_NESTING]


@pytest.mark.parametrize(
    "document", [_array(PARSER_RECURSION_DEPTH), _object(PARSER_RECURSION_DEPTH), "[" * PARSER_RECURSION_DEPTH]
)
def test_a_document_deeper_than_the_parser_can_read_is_refused_not_recursed_into(document: str) -> None:
    assert _refusal(document).msg == f"Nesting is deeper than {MAX_NESTING} levels"


def test_levels_that_have_closed_do_not_add_up() -> None:
    siblings = "[" + ",".join([_array(MAX_NESTING - 1)] * 200) + "]"

    assert load_clean_json(siblings) == json.loads(siblings)
    assert _refusal("[" + ",".join([_array(MAX_NESTING)] * 3) + "]").pos == MAX_NESTING


@pytest.mark.parametrize("member", ["[1,2]", '{"a": 1}'], ids=["arrays", "objects"])
def test_a_wide_document_loads(member: str) -> None:
    wide = "[" + ",".join([member] * 50_000) + "]"

    assert load_clean_json(wide) == json.loads(wide)


@pytest.mark.parametrize(
    "document",
    [
        '["' + "[" * 1000 + '"]',
        '{"' + "{" * 1000 + '": "' + "[" * 1000 + '"}',
        '["a\\"' + "[" * 200 + '"]',
        '["a\\\\"]',
    ],
    ids=[
        "brackets in a string",
        "brackets in a key and a value",
        "brackets after an escaped quote",
        "escaped backslash",
    ],
)
def test_a_bracket_inside_a_string_is_no_level(document: str) -> None:
    assert load_clean_json(document) == json.loads(document)


@pytest.mark.parametrize(
    "prefix",
    ['["a\\\\", ', '["a\\"b", ', '{"k\\\\": '],
    ids=["after an escaped backslash", "after an escaped quote", "after a key ending in an escaped backslash"],
)
def test_a_string_does_not_hide_the_levels_that_follow_it(prefix: str) -> None:
    document = prefix + _array(MAX_NESTING) + ("]" if prefix.startswith("[") else "}")

    assert _refusal(document).msg == f"Nesting is deeper than {MAX_NESTING} levels"


def test_a_string_left_open_hides_the_brackets_after_it_so_the_parser_reports_the_syntax_error() -> None:
    error = _refusal('["' + "[" * 200)

    assert error.msg.startswith("Unterminated string")


def test_a_backslash_escapes_a_line_break_too_so_the_brackets_after_it_stay_inside_the_string() -> None:
    # A literal pattern that reads an escape as a backslash and any character but a line break fails here, and the brackets
    # after it would count as levels instead of the string text the parser reports a bad escape in.
    error = _refusal('["\\\n' + "[" * (MAX_NESTING + 5) + '"]')

    assert error.msg.startswith("Invalid \\escape")


def _words(longest: int, *, shortest: int) -> list[str]:
    lengths = range(shortest, longest + 1)
    return ["".join(word) for length in lengths for word in itertools.product(SCAN_SYMBOLS, repeat=length)]


def _cpu_seconds(document: str) -> float:
    # The CPU time of this thread alone, so that another thread of the process cannot be charged to the scan.
    start = time.thread_time()
    with contextlib.suppress(json.JSONDecodeError):
        load_clean_json(document)
    return time.thread_time() - start


def test_scanning_text_that_piles_up_quotes_backslashes_and_line_breaks_stays_within_a_cpu_budget() -> None:
    # Enough openers to get past the cheap count, balanced so that the scan never stops early.
    guard = "[]" * (MAX_NESTING + 6)
    slow: list[tuple[float, str, str, str]] = []
    for opener, pump, tail in itertools.product(("", '"'), _words(2, shortest=1), _words(2, shortest=0)):
        document = guard + opener + pump * PUMP_REPEATS + tail
        spent = _cpu_seconds(document)
        if spent > SCAN_CPU_BUDGET:  # measure again, so that one pause of the process cannot fail the test
            spent = min(spent, _cpu_seconds(document), _cpu_seconds(document))
        if spent > SCAN_CPU_BUDGET:
            slow.append((spent, opener, pump, tail))

    slowest = sorted(slow, reverse=True)[:3]
    assert not slow, f"{len(slow)} shapes took over {SCAN_CPU_BUDGET} s; the slowest (s, opener, pump, tail): {slowest}"


def test_scanning_a_string_literal_of_escapes_holds_memory_within_twice_the_text() -> None:
    # More openers than the limit, so that the scan runs; a valid document, so that the parser accepts it.
    document = "[" + "[]," * MAX_NESTING + '"' + '\\"' * (ONE_MIB // 2) + '"]'

    tracemalloc.start()
    try:
        load_clean_json(document)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 2 * len(document)
