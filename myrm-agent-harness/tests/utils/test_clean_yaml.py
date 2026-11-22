"""Tests for load_clean_yaml: ``yaml.safe_load`` whose strings can all be encoded as UTF-8.

[INPUT]
- utils.clean_yaml::load_clean_yaml (POS: parse step for documents from outside the process)
- yaml (POS: the parser whose ``safe_load`` it must agree with)
- utils.text_sanitizer::encodable_text (POS: the repair applied once per string node, whose calls the alias test counts)

[OUTPUT]
- Fails when a lone surrogate (a double-quoted surrogate escape in the document) survives into the result, when anything
  else a ``safe_load`` keeps is changed, when an error differs, or when an alias or a self-reference is handled per reference.

[POS]
One such escape in a frontmatter would otherwise fail every response that carries the parsed text.
"""

from __future__ import annotations

import datetime

import pytest
import yaml

from myrm_agent_harness.utils import clean_yaml
from myrm_agent_harness.utils.clean_yaml import load_clean_yaml
from myrm_agent_harness.utils.text_sanitizer import encodable_text

LONE = r"\ud800"  # the escape as it appears in the YAML text; PyYAML decodes it to one lone surrogate
REPLACED = "\ufffd"


def _strings(document: object) -> list[str]:
    """Every string, key or value, at any depth of a parsed document."""
    found: list[str] = []
    pending: list[object] = [document]
    seen: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict | list | set) and id(item) not in seen:
            seen.add(id(item))
            pending.extend(item.keys() if isinstance(item, dict) else [])
            pending.extend(item.values() if isinstance(item, dict) else item)
    return found


def test_the_failure_this_prevents_is_real() -> None:
    parsed = yaml.safe_load(f'description: "a{LONE}b"')

    with pytest.raises(UnicodeEncodeError):
        str(parsed["description"]).encode("utf-8")


def test_a_lone_surrogate_in_a_value_becomes_the_replacement_character() -> None:
    assert load_clean_yaml(f'description: "a{LONE}b"') == {"description": f"a{REPLACED}b"}


def test_a_surrogate_pair_escape_is_two_replacements() -> None:
    assert load_clean_yaml(r'x: "\ud83d\ude00"') == {"x": REPLACED * 2}


def test_keys_nested_values_and_flow_collections_are_repaired() -> None:
    document = f"""
"k{LONE}": {{inner: ["a{LONE}", {{deep: "b{LONE}"}}], "x{LONE}": 1}}
list:
  - "c{LONE}"
  - - "d{LONE}"
"""
    parsed = load_clean_yaml(document)

    assert _strings(parsed)
    for text in _strings(parsed):
        text.encode("utf-8")
    assert parsed == {
        f"k{REPLACED}": {"inner": [f"a{REPLACED}", {"deep": f"b{REPLACED}"}], f"x{REPLACED}": 1},
        "list": [f"c{REPLACED}", [f"d{REPLACED}"]],
    }


def test_everything_but_the_lone_surrogates_is_kept() -> None:
    # U+0085 and ESC are control characters a strip would delete; the tab and the newline are ordinary text.
    parsed = load_clean_yaml(f'x: "a\\x85b\\x1bc\\td\\ne{LONE}"')

    assert parsed == {"x": f"a\x85b\x1bc\td\ne{REPLACED}"}


@pytest.mark.parametrize(
    "document",
    [
        "name: plain\ncount: 3\nratio: 0.5\nflag: true\nnothing: null\nempty:\n",
        "when: 2026-11-18\nstamp: 2026-11-18T10:20:30Z\nhex: 0x1f\noctal: 0o17\ninf: .inf",
        "text: |\n  line one\n  line two\nfolded: >\n  a\n  b\nquoted: 'it''s'\nunicode: \u4e2d\u6587 caf\u00e9",
        "base: &b {a: 1, b: two}\nderived:\n  <<: *b\n  c: 3\nagain: *b",
        "blob: !!binary aGVsbG8=\nunique: !!set {a, b}\nordered: !!omap [a: 1, b: 2]",
        "- a\n- [b, c]\n- {d: e}\n- - f",
        "just a scalar",
        "",
    ],
)
def test_an_ordinary_document_loads_exactly_as_safe_load_loads_it(document: str) -> None:
    assert load_clean_yaml(document) == yaml.safe_load(document)


def test_the_types_safe_load_builds_are_unchanged() -> None:
    parsed = load_clean_yaml("when: 2026-11-18\nstamp: 2026-11-18T10:20:30\nblob: !!binary aGVsbG8=")

    assert isinstance(parsed, dict)
    assert type(parsed["when"]) is datetime.date
    assert type(parsed["stamp"]) is datetime.datetime
    assert parsed["blob"] == b"hello"


@pytest.mark.parametrize("document", ["a: [unclosed", "a: b: c", "key: 'unterminated", "\t: tab-indented key"])
def test_a_syntax_error_raises_what_safe_load_raises(document: str) -> None:
    with pytest.raises(yaml.YAMLError) as expected:
        yaml.safe_load(document)

    with pytest.raises(type(expected.value)) as raised:
        load_clean_yaml(document)

    assert str(raised.value) == str(expected.value)


def test_a_second_document_is_refused_like_safe_load() -> None:
    with pytest.raises(yaml.composer.ComposerError):
        load_clean_yaml("a: 1\n---\nb: 2")


def test_a_python_object_tag_is_still_refused() -> None:
    with pytest.raises(yaml.constructor.ConstructorError):
        load_clean_yaml('!!python/object/apply:os.system ["echo unsafe"]')


def test_an_alias_is_the_very_object_its_anchor_built() -> None:
    parsed = load_clean_yaml(f'base: &s "x{LONE}"\nuse: *s\nlist: [*s, *s]')

    assert isinstance(parsed, dict)
    assert parsed["use"] is parsed["base"]
    assert parsed["list"][0] is parsed["base"]
    assert parsed["base"] == f"x{REPLACED}"


def test_a_self_referencing_alias_loads_as_it_does_under_safe_load() -> None:
    parsed = load_clean_yaml("&a [*a]")

    assert isinstance(parsed, list)
    assert parsed[0] is parsed


def test_an_alias_fan_out_is_not_walked_per_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nine levels of nine aliases each stand for about 387 million strings; only the nodes of the text are built.
    levels = ['a: &a ["lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol"]']
    previous = "a"
    for name in "bcdefghi":
        levels.append(f"{name}: &{name} [{', '.join([f'*{previous}'] * 9)}]")
        previous = name
    calls: list[str] = []

    def counting_encodable_text(text: str) -> str:
        calls.append(text)
        return encodable_text(text)

    monkeypatch.setattr(clean_yaml, "encodable_text", counting_encodable_text)

    parsed = load_clean_yaml("\n".join(levels))

    assert isinstance(parsed, dict)
    assert parsed["i"][0] is parsed["i"][8]
    assert len(calls) == 9 + 9  # nine keys and nine "lol" scalars
