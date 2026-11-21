"""Tests for where the files a package.json declares live on disk.

[INPUT]
- backends.skills.scanning.manifest_entries::collect_export_targets, resolve_artifact_file (POS: the helpers under test)

[OUTPUT]
- Fails when the ``exports`` targets lose their document order or keep a blank or non-string value, when a value nested
  beyond the recursion limit raises, when an entry resolves to another file than the one chosen here (the file itself, then
  an extension, then an index file), or when a name the file system rejects resolves to anything.

[POS]
The entry-point artifact check of ``package_audit`` asks these two helpers what a manifest declares and where it points;
what it makes of the answer (a missing or empty artifact) is tested with ``package_audit``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from myrm_agent_harness.backends.skills.scanning.manifest_entries import collect_export_targets, resolve_artifact_file


def _write(root: Path, *relatives: str) -> None:
    for relative in relatives:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")


def test_export_targets_keep_document_order_and_drop_what_is_not_a_path() -> None:
    exports = {
        ".": {"import": "./a.mjs", "require": " ./b.cjs "},
        "./list": ["./c.js", {"default": "./d.js"}],
        "./blank": "   ",
        "./number": 1,
        "./null": None,
    }

    assert collect_export_targets(exports) == ["./a.mjs", "./b.cjs", "./c.js", "./d.js"]


def test_a_bare_string_export_is_its_own_target() -> None:
    assert collect_export_targets("./index.js") == ["./index.js"]


def test_export_targets_of_a_value_nested_beyond_the_recursion_limit() -> None:
    node: object = "./leaf.js"
    for _ in range(sys.getrecursionlimit() * 2):
        node = {"default": node}

    assert collect_export_targets(node) == ["./leaf.js"]


def test_a_file_named_exactly_wins_over_one_with_an_extension(tmp_path: Path) -> None:
    _write(tmp_path, "lib/main", "lib/main.js")

    assert resolve_artifact_file(tmp_path, "lib/main") == tmp_path / "lib" / "main"


@pytest.mark.parametrize(
    ("present", "expected"),
    [
        (("main.js", "main.cjs", "main.mjs", "main.json"), "main.js"),
        (("main.cjs", "main.mjs", "main.json"), "main.cjs"),
        (("main.mjs", "main.json"), "main.mjs"),
        (("main.json",), "main.json"),
    ],
)
def test_extensions_are_tried_in_the_order_js_cjs_mjs_json(
    tmp_path: Path, present: tuple[str, ...], expected: str
) -> None:
    _write(tmp_path, *(f"lib/{name}" for name in present))

    assert resolve_artifact_file(tmp_path, "lib/main") == tmp_path / "lib" / expected


def test_a_file_with_an_extension_wins_over_a_directory_index(tmp_path: Path) -> None:
    _write(tmp_path, "lib.js", "lib/index.js")

    assert resolve_artifact_file(tmp_path, "lib") == tmp_path / "lib.js"


def test_a_directory_resolves_to_its_index_file(tmp_path: Path) -> None:
    _write(tmp_path, "lib/index.mjs")

    assert resolve_artifact_file(tmp_path, "lib") == tmp_path / "lib" / "index.mjs"


def test_nothing_resolves_when_no_file_matches(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()

    assert resolve_artifact_file(tmp_path, "lib") is None


def test_a_name_the_file_system_rejects_resolves_to_nothing(tmp_path: Path) -> None:
    assert resolve_artifact_file(tmp_path, "a" * 300) is None
