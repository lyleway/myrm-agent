"""Tests for the digit limit of load_clean_json: an integer with more digits than the interpreter converts is refused as a decode error at the number.

[INPUT]
- utils.clean_json::load_clean_json (POS: parse step for the plugin.json and mcp.json of a plugin archive)
- json (POS: the parser whose ``loads`` the loader must agree with up to the limit)

[OUTPUT]
- Fails when an integer of as many digits as the limit is refused or changes, when one with more digits raises anything but a
  ``json.JSONDecodeError`` at its first digit, when that error carries the interpreter's advice to raise its limit, when digits
  inside a string count or place the error, when a syntax error before the number is no longer the parser's own, or when
  finding the number costs more than a fixed CPU budget on text built to make a scan read the same characters over and over.

[POS]
``json.loads`` raises a bare ``ValueError`` for such an integer, which a caller that catches ``json.JSONDecodeError`` lets
escape; a hostile plugin.json or mcp.json would then end the import preview in a refusal that carries the interpreter's advice
instead of a diagnostic.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from collections.abc import Callable, Iterator

import pytest

from myrm_agent_harness.utils.clean_json import load_clean_json

LIMIT = sys.get_int_max_str_digits()
pytestmark = pytest.mark.skipif(not LIMIT, reason="the interpreter converts integers of any length")

AT_LIMIT = "9" * LIMIT
OVER_LIMIT = "9" * (LIMIT + 1)
ONE_MIB = 1 << 20  # as much as a plugin document may hold
SEARCH_CPU_BUDGET = 0.5  # seconds: far above reading the text once, far below starting the search again at every offset

PLACES: dict[str, Callable[[str], str]] = {
    "alone": lambda number: number,
    "in an array": lambda number: f"[1, {number}]",
    "as a member": lambda number: f'{{"n": {number}}}',
    "below a member": lambda number: f'{{"a": [{{"n": {number}}}]}}',
    "after a sign": lambda number: f"[-{number}]",
}

# Each text ends in the integer over the limit, so the search has to read all of what comes before it. Starting a search again
# at every offset reads each run of as many digits as the limit again from every digit of it.
HOSTILE_TEXTS: dict[str, Callable[[], str]] = {
    "runs of as many digits as the limit": lambda: (
        "[" + ",".join([AT_LIMIT] * (ONE_MIB // (LIMIT + 1) - 1)) + f",{OVER_LIMIT}]"
    ),
    "escaped quotes": lambda: '["' + '\\"' * (ONE_MIB // 2 - 8) + f'", {OVER_LIMIT}]',
    "one-digit integers": lambda: "[" + "1," * (ONE_MIB // 2 - 8) + f"{OVER_LIMIT}]",
}


def _refusal(document: str) -> json.JSONDecodeError:
    with pytest.raises(json.JSONDecodeError) as caught:
        load_clean_json(document)
    return caught.value


@pytest.mark.parametrize("place", PLACES)
def test_an_integer_of_as_many_digits_as_the_limit_loads_as_json_loads_does(place: str) -> None:
    document = PLACES[place](AT_LIMIT)

    assert load_clean_json(document) == json.loads(document)


@pytest.mark.parametrize("place", PLACES)
def test_an_integer_over_the_limit_is_refused_at_its_first_digit(place: str) -> None:
    document = PLACES[place](OVER_LIMIT)

    error = _refusal(document)

    assert error.msg == f"Number has more than {LIMIT} digits"
    assert error.pos == document.index("9")


def test_the_refusal_carries_nothing_of_the_interpreters_own_message() -> None:
    error = _refusal(f"[{OVER_LIMIT}]")

    assert str(error) == f"Number has more than {LIMIT} digits: line 1 column 2 (char 1)"
    assert error.__cause__ is None


@pytest.fixture
def lowest_limit() -> Iterator[int]:
    previous = sys.get_int_max_str_digits()
    lowest = 640  # the least the interpreter accepts apart from 0, which lifts the limit
    sys.set_int_max_str_digits(lowest)
    yield lowest
    sys.set_int_max_str_digits(previous)


def test_the_limit_is_the_one_the_interpreter_has_when_the_document_is_read(lowest_limit: int) -> None:
    document = f"[{'9' * (lowest_limit + 1)}]"

    assert load_clean_json(f"[{'9' * lowest_limit}]") == json.loads(f"[{'9' * lowest_limit}]")
    assert _refusal(document).msg == f"Number has more than {lowest_limit} digits"


def test_digits_inside_a_string_are_no_integer() -> None:
    document = f'{{"id": "{OVER_LIMIT}", "n": {AT_LIMIT}}}'

    assert load_clean_json(document) == json.loads(document)


def test_digits_inside_a_string_do_not_place_the_refusal() -> None:
    # An escaped quote does not end the string, so the digits after it are still inside it.
    document = f'["{OVER_LIMIT}\\"{OVER_LIMIT}", {OVER_LIMIT}]'

    assert _refusal(document).pos == document.rindex("9") - LIMIT


def test_a_float_with_a_long_integer_part_loads() -> None:
    document = f"[{OVER_LIMIT}.5]"

    assert load_clean_json(document) == json.loads(document)


def test_a_syntax_error_before_the_number_stays_the_parsers_own() -> None:
    assert _refusal(f"[,{OVER_LIMIT}]").msg == "Expecting value"


def _cpu_seconds(document: str) -> float:
    # The CPU time of this thread alone, so that another thread of the process cannot be charged to the search.
    start = time.thread_time()
    with contextlib.suppress(json.JSONDecodeError):
        load_clean_json(document)
    return time.thread_time() - start


@pytest.mark.parametrize("shape", HOSTILE_TEXTS)
def test_finding_the_number_in_text_built_to_slow_a_scan_stays_within_a_cpu_budget(shape: str) -> None:
    document = HOSTILE_TEXTS[shape]()

    spent = _cpu_seconds(document)
    if spent > SEARCH_CPU_BUDGET:  # measure again, so that one pause of the process cannot fail the test
        spent = min(spent, _cpu_seconds(document), _cpu_seconds(document))

    assert spent < SEARCH_CPU_BUDGET, f"{shape}: {spent:.2f} s for {len(document)} bytes"
