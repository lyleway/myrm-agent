"""Tests for load_clean_json: ``json.loads`` whose strings can all be encoded as UTF-8.

[INPUT]
- utils.clean_json::load_clean_json (POS: parse step for the plugin.json and mcp.json of a plugin archive)
- json (POS: the parser whose ``loads`` it must agree with)
- utils.text_sanitizer::encodable_text (POS: the repair applied after the parse, whose result the text-level rewrite must equal)

[OUTPUT]
- Fails when a lone surrogate (a surrogate escape or character in the document) survives into the result, when anything
  else ``json.loads`` keeps is changed, when an error differs, or when an escaped backslash is read as the start of an escape.

[POS]
One such escape in a plugin.json or mcp.json would otherwise fail every response that carries the parsed text.
"""

from __future__ import annotations

import itertools
import json

import pytest

from myrm_agent_harness.utils import clean_json
from myrm_agent_harness.utils.clean_json import load_clean_json
from myrm_agent_harness.utils.text_sanitizer import encodable_text

LONE = r"\ud800"  # the escape as it appears in the JSON text; json.loads decodes it to one lone surrogate
REPLACED = "\ufffd"
GRINNING_FACE = "\U0001f600"
# Pieces of a JSON string body: the first and last high and low surrogate escape, upper-case digits, the code points on either
# side of the surrogate range, and the backslash forms that decide what is an escape.
TOKENS = [
    r"\ud800",
    r"\udbff",
    r"\udc00",
    r"\udfff",
    r"\uD83D",
    r"\uDE00",
    r"\ud7ff",
    r"\ue000",
    "\\\\",
    "\\",
    "u",
    "d800",
    "a",
]


def _strings(document: object) -> list[str]:
    """Every string, key or value, at any depth of a parsed document."""
    found: list[str] = []
    pending: list[object] = [document]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return found


def test_the_failure_this_prevents_is_real() -> None:
    parsed = json.loads(f'{{"description": "a{LONE}b"}}')

    with pytest.raises(UnicodeEncodeError):
        str(parsed["description"]).encode("utf-8")


def test_a_lone_surrogate_in_a_value_becomes_the_replacement_character() -> None:
    assert load_clean_json(f'{{"description": "a{LONE}b"}}') == {"description": f"a{REPLACED}b"}


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (r'"\ud83d\ude00"', GRINNING_FACE),
        (r'"\uD83D\uDE00"', GRINNING_FACE),
        (r'"\ud800\udc00"', "\U00010000"),
        (r'"\udbff\udfff"', "\U0010ffff"),
        (r'"\ud800\ud83d\ude00"', REPLACED + GRINNING_FACE),
        (r'"\ud83d\ud83d\ude00"', REPLACED + GRINNING_FACE),
        (r'"\ud83d\u0041"', REPLACED + "A"),
        (r'"\ud83d"', REPLACED),
        (r'"\ude00"', REPLACED),
        (r'"\ude00\ud83d"', REPLACED * 2),
    ],
)
def test_an_escaped_pair_is_the_character_it_spells_and_any_other_surrogate_escape_is_replaced(
    document: str, expected: str
) -> None:
    assert load_clean_json(document) == expected


def test_every_surrogate_escape_is_replaced_in_either_case_and_the_code_units_beside_the_range_are_kept() -> None:
    for code in range(0xD800, 0xE000):
        for digits in (f"{code:04x}", f"{code:04X}"):
            assert load_clean_json(f'"\\u{digits}"') == REPLACED
    assert load_clean_json(r'"\ud7ff\ue000"') == "\ud7ff\ue000"


def test_every_high_surrogate_escape_followed_by_a_low_one_is_the_character_the_pair_spells() -> None:
    lows = (0xDC00, 0xDC01, 0xDD55, 0xDE00, 0xDFFE, 0xDFFF)
    for high in range(0xD800, 0xDC00):
        for low in lows:
            expected = chr(0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00))
            for digits in (f"{high:04x}\\u{low:04x}", f"{high:04X}\\u{low:04X}"):
                assert load_clean_json(f'"\\u{digits}"') == expected


def test_keys_and_nested_values_are_repaired() -> None:
    document = f'{{"k{LONE}": {{"inner": ["a{LONE}", {{"deep": "b{LONE}"}}], "x{LONE}": 1}}, "list": [["c{LONE}"]]}}'

    parsed = load_clean_json(document)

    assert _strings(parsed)
    for text in _strings(parsed):
        text.encode("utf-8")
    assert parsed == {
        f"k{REPLACED}": {"inner": [f"a{REPLACED}", {"deep": f"b{REPLACED}"}], f"x{REPLACED}": 1},
        "list": [[f"c{REPLACED}"]],
    }


def test_nesting_is_followed_by_the_parser_and_not_by_the_repair() -> None:
    depth = 300
    parsed = load_clean_json('{"a":' * depth + f'"x{LONE}"' + "}" * depth)

    for _ in range(depth):
        assert isinstance(parsed, dict)
        parsed = parsed["a"]
    assert parsed == f"x{REPLACED}"


def test_everything_but_the_lone_surrogates_is_kept() -> None:
    # U+0085 and ESC are control characters a strip would delete; the tab and the newline are ordinary text.
    parsed = load_clean_json(rf'"a\u0085b\u001bc\td\ne\\f\/g\"h{LONE}"')

    assert parsed == f'a\x85b\x1bc\td\ne\\f/g"h{REPLACED}'


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (r'"\\ud800"', "\\ud800"),
        (r'"\\\ud800"', "\\" + REPLACED),
        (r'"\\\\ud800"', "\\\\ud800"),
        (r'"\\\\\ud800"', "\\\\" + REPLACED),
    ],
)
def test_an_escaped_backslash_is_text_and_never_the_start_of_an_escape(document: str, expected: str) -> None:
    assert load_clean_json(document) == expected


def test_a_long_run_of_backslashes_is_paired_from_the_left() -> None:
    pairs = 100_000

    assert load_clean_json('"' + "\\\\" * pairs + 'ud800"') == "\\" * pairs + "ud800"
    assert load_clean_json('"' + "\\\\" * pairs + '\\ud800"') == "\\" * pairs + REPLACED


class _ForbiddenRewrite:
    def sub(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("the rewrite ran for a text that holds no surrogate-shaped escape")


@pytest.mark.parametrize(
    "document",
    [
        '{"text": "plain", "numbers": [1, 2.5]}',
        r'{"text": "\u4e2d\u6587 caf\u00e9 \n\t\\"}',
        r'{"text": "\ud55c\uae00 \ud7a3 \ue000"}',
    ],
)
def test_a_text_without_a_surrogate_shaped_escape_skips_the_rewrite(
    document: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CJK and Korean text written by json.dumps is full of \u escapes that are no surrogates; the rewrite calls back for each.
    monkeypatch.setattr(clean_json, "_ESCAPE", _ForbiddenRewrite())

    assert load_clean_json(document) == json.loads(document)


def test_a_lone_surrogate_character_in_the_text_is_replaced_too() -> None:
    assert load_clean_json('{"x": "a\ud800b", "k\udfff": 1}') == {"x": f"a{REPLACED}b", f"k{REPLACED}": 1}


def test_two_keys_that_differ_only_in_their_lone_surrogates_become_one_key_and_the_later_value_wins() -> None:
    assert load_clean_json(rf'{{"k{LONE}": 1, "k\udfff": 2}}') == {f"k{REPLACED}": 2}


@pytest.mark.parametrize(
    "document",
    [
        '{"name": "plain", "count": 3, "ratio": 0.5, "big": 1e400, "flag": true, "nothing": null, "empty": {}, "none": []}',
        '[1, -2, 3.25, 1E-3, Infinity, -Infinity, "text"]',
        '{"unicode": "\u4e2d\u6587 caf\u00e9", "escaped": "\\u4e2d\\u6587 caf\\u00e9 \\ud83d\\ude00", "text": "a\\nb\\t\\"c\\"\\\\"}',
        '  {\n  "nested": {"list": [{"a": [1, [2, [3]]]}]}\n}\n',
        '"just a string"',
        "3",
        "null",
    ],
)
def test_an_ordinary_document_loads_exactly_as_json_loads_loads_it(document: str) -> None:
    assert load_clean_json(document) == json.loads(document)


@pytest.mark.parametrize(
    "document",
    [
        '{"a": ',
        '{"a" 1}',
        "[1, 2,]",
        '{"a": "\\uZZZZ"}',
        '{"a": "x\\ud800',
        f'{{"a": "x{LONE}y", "b": }}',
        f'{{"a": "x{LONE}\\ud83d\\ude00\\\\{LONE}", "b" "c"}}',
        "",
        "\ufeff{}",
    ],
)
def test_a_syntax_error_raises_what_json_loads_raises_at_the_position_it_reports(document: str) -> None:
    with pytest.raises(json.JSONDecodeError) as expected:
        json.loads(document)

    with pytest.raises(json.JSONDecodeError) as raised:
        load_clean_json(document)

    assert str(raised.value) == str(expected.value)


def _reference(document: str) -> str:
    """What the document means: the parse first, then every lone surrogate of its one string replaced."""
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError as error:
        return f"error: {error}"
    assert isinstance(parsed, str)
    return encodable_text(parsed)


def _loaded(document: str) -> str:
    try:
        parsed = load_clean_json(document)
    except json.JSONDecodeError as error:
        return f"error: {error}"
    assert isinstance(parsed, str)
    return parsed


def test_the_rewrite_before_the_parse_means_what_a_repair_after_the_parse_means() -> None:
    documents = [
        '"' + "".join(pieces) + '"' for length in range(5) for pieces in itertools.product(TOKENS, repeat=length)
    ]

    assert len(documents) == sum(len(TOKENS) ** length for length in range(5))
    assert [document for document in documents if _loaded(document) != _reference(document)] == []
