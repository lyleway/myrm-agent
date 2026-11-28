"""Tests for the MCP server identity rule (``server_identity.py``).

Pins what the registry, approval, eviction and per-tool permission keys depend on: an ASCII name keeps the segment it
always had (up to the length every tool name can still carry), every other name gets a stable hash suffix so distinct
names stay distinct, no identity outgrows that length, and the TypeScript twin (``mcpServerIdentity.ts``) reads the same
vectors from ``shared/config/mcp_server_identity.json``.
"""

from __future__ import annotations

import json
import random
import re
import string
import unicodedata
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.mcp.config import mcp_tool_prefix, parse_mcp_tool_name
from myrm_agent_harness.toolkits.mcp.server_identity import _stable_suffix, fold_mcp_name, mcp_server_component
from myrm_agent_harness.toolkits.mcp.tool_name import MAX_IDENTITY_LENGTH

_VECTORS_FILE = Path(__file__).resolve().parents[4] / "shared" / "config" / "mcp_server_identity.json"
# The harness can be tested without the rest of the monorepo next to it; the vector tests then have nothing to read.
_VECTORS: list[dict[str, str]] = (
    json.loads(_VECTORS_FILE.read_text(encoding="utf-8")) if _VECTORS_FILE.is_file() else []
)
_VECTOR_IDS = [repr(case["name"]) for case in _VECTORS]

_UNICODE_ALPHABET = string.printable + "天气我的服务日本語サーバー서버😀é\u0301ß\u200b\ud800\udfff"


def _legacy_component(name: str) -> str:
    """The segment every ASCII name had before the hash suffix existed, written out independently of the module."""
    result = re.sub(r"[^A-Za-z0-9_]", "_", name)
    result = re.sub(r"_{2,}", "_", result)
    return result.strip("_") or "unnamed"


@pytest.mark.parametrize("case", _VECTORS, ids=_VECTOR_IDS)
def test_the_shared_vectors_hold(case: dict[str, str]) -> None:
    assert mcp_server_component(case["name"]) == case["identity"]


@pytest.mark.parametrize("case", _VECTORS, ids=_VECTOR_IDS)
def test_the_prefix_wraps_the_identity_and_parses_back(case: dict[str, str]) -> None:
    prefix = mcp_tool_prefix(case["name"])

    assert prefix == f"mcp__{case['identity']}__"
    assert parse_mcp_tool_name(f"{prefix}tool") == (case["identity"], "tool")


def test_distinct_names_in_the_vectors_get_distinct_identities_except_spellings_of_one_name() -> None:
    by_identity: dict[str, set[str]] = {}
    for case in _VECTORS:
        by_identity.setdefault(case["identity"], set()).add(unicodedata.normalize("NFC", case["name"]))

    # Only the ASCII separator variants of one name (``my-api``, ``my_api``, ``my api``) share an identity: folding keeps
    # them one name on purpose, and the registration points reject the second one.
    shared = {identity: names for identity, names in by_identity.items() if len(names) > 1}
    assert shared == {"my_api": {"my-api", "my_api", "my api"}}


def test_no_server_prefix_is_the_start_of_another_servers_tool_names() -> None:
    prefixes = {mcp_tool_prefix(case["name"]) for case in _VECTORS}

    for prefix in prefixes:
        for other in prefixes - {prefix}:
            assert not f"{other}tool".startswith(prefix)


def test_ascii_names_that_survive_folding_keep_the_segment_they_always_had() -> None:
    rng = random.Random(20261127)
    for _ in range(3000):
        name = "".join(rng.choice(string.printable) for _ in range(rng.randint(1, 24)))
        if fold_mcp_name(name):
            assert mcp_server_component(name) == _legacy_component(name), repr(name)


def test_ascii_names_up_to_the_bound_keep_the_segment_they_always_had() -> None:
    rng = random.Random(20261130)
    alphabet = string.ascii_letters + string.digits + "_-. "
    for _ in range(3000):
        name = "".join(rng.choice(alphabet) for _ in range(rng.randint(25, 70)))
        folded = fold_mcp_name(name)
        if 0 < len(folded) <= MAX_IDENTITY_LENGTH:
            assert mcp_server_component(name) == _legacy_component(name), repr(name)


@pytest.mark.parametrize("length", [MAX_IDENTITY_LENGTH - 1, MAX_IDENTITY_LENGTH])
def test_an_ascii_name_of_the_bound_or_less_is_its_own_identity(length: int) -> None:
    assert mcp_server_component("a" * length) == "a" * length


def test_a_longer_ascii_name_keeps_a_readable_head_and_one_hash_for_all_its_spellings() -> None:
    identity = mcp_server_component("abcde-" * 10)

    assert identity == "abcde_abcde_abcde_abcde_abcde_abcde_abcde_746341"  # the cut never leaves a trailing underscore
    assert mcp_server_component("abcde_" * 10) == identity
    assert mcp_server_component("abcde " * 10) == identity


def test_no_identity_outgrows_the_bound() -> None:
    rng = random.Random(20261201)
    for _ in range(3000):
        name = "".join(rng.choice(_UNICODE_ALPHABET) for _ in range(rng.randint(0, 200)))

        assert len(mcp_server_component(name)) <= MAX_IDENTITY_LENGTH, repr(name)

    assert len(mcp_server_component("天气" * 200)) <= MAX_IDENTITY_LENGTH
    assert len(mcp_server_component("x" * 5000)) <= MAX_IDENTITY_LENGTH


def test_a_name_made_only_of_symbols_is_still_told_apart_however_long_it_is() -> None:
    assert mcp_server_component("-" * 100) != mcp_server_component("-" * 101)
    assert mcp_server_component("天" * 100) != mcp_server_component("天" * 101)
    assert re.fullmatch(r"unnamed_[0-9a-f]{6}", mcp_server_component("天" * 100))


def test_an_identity_is_never_empty_and_never_holds_the_tool_delimiter() -> None:
    rng = random.Random(20261128)
    for _ in range(3000):
        name = "".join(rng.choice(_UNICODE_ALPHABET) for _ in range(rng.randint(0, 24)))
        identity = mcp_server_component(name)

        assert identity, repr(name)
        assert "__" not in identity, repr(name)
        assert identity == identity.strip("_"), repr(name)
        assert re.fullmatch(r"[A-Za-z0-9_]+", identity), repr(name)


def test_composed_and_decomposed_spellings_are_one_server() -> None:
    assert mcp_server_component("caf\u00e9") == mcp_server_component("cafe\u0301")


def test_a_name_with_a_lone_surrogate_still_gets_an_identity() -> None:
    """Half of a cut emoji must not make the rule raise: it hashes as U+FFFD, the way JavaScript encodes it."""
    assert mcp_server_component("\ud800") == "unnamed_03479c"
    assert mcp_server_component("a\ud83d") == "a_d93a90"
    assert mcp_server_component("\ud83d\ude00") == mcp_server_component("\U0001f600")


def test_a_name_stacking_a_huge_run_of_combining_marks_still_gets_an_identity() -> None:
    """A plugin can declare any name: marks in alternating classes must neither stall the rule nor change its shape."""
    assert re.fullmatch(r"x_[0-9a-f]{6}", mcp_server_component("x" + "\u0301\u0316" * 20_000))


def test_a_name_that_folds_to_nothing_is_still_told_apart_from_its_peers() -> None:
    assert mcp_server_component("---") != mcp_server_component("   ")
    assert mcp_server_component("") != mcp_server_component("---")
    assert mcp_server_component("unnamed") == "unnamed"


@pytest.mark.parametrize(
    ("text", "suffix"),
    [("", "811c9d"), ("a", "e40c29"), ("foobar", "bf9cf9")],
)
def test_the_suffix_is_the_standard_fnv1a_32_hash(text: str, suffix: str) -> None:
    """The published 32-bit FNV-1a test vectors (0x811c9dc5, 0xe40c292c, 0xbf9cf968), first six hex digits."""
    assert _stable_suffix(text) == suffix
