"""Tests for what the package.json audit does about the files and folders around a manifest.

[INPUT]
- backends.skills.scanning.package_audit::audit_skill_directory, check_lifecycle_scripts, PackageAuditFinding (POS: the audit under test)
- backends.skills.scanning.directory_scanner::scan_skill_directory (POS: the directory scan that carries its findings to the adopt gate)
- backends.skills.scanning.scanner::compute_scan_summary (POS: the score the adopt gate compares with its threshold)
- backends.skills.scanning.tree_walk (POS: the walk whose bounds are lowered here)

[OUTPUT]
- Fails when a manifest is skipped for its depth or folder, when a file a package manager runs at install without a script
  reads as clean, when a manifest beyond the audit's cap, a manifest over 1 MB, a folder it cannot list or a tree over its
  entry budget reads as clean, or when an ordinary skill tree stops reading as clean.

[POS]
``test_package_audit.py`` and ``test_package_audit_unauditable.py`` cover a single manifest; this file pins what the audit
sees around it.
"""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from myrm_agent_harness.backends.skills.scanning import tree_walk
from myrm_agent_harness.backends.skills.scanning.directory_scanner import scan_skill_directory
from myrm_agent_harness.backends.skills.scanning.package_audit import (
    PackageAuditFinding,
    audit_skill_directory,
    check_lifecycle_scripts,
)
from myrm_agent_harness.backends.skills.scanning.scanner import compute_scan_summary

_POSTINSTALL = json.dumps({"name": "t", "scripts": {"postinstall": "node run.js"}})
_HOOK_FILES = [".npm-extension.cjs", ".npm-extension.mjs", ".pnpmfile.cjs", ".pnpmfile.mjs", "binding.gyp"]
_LIMIT = 1024 * 1024


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _summary(findings: list[PackageAuditFinding]) -> list[tuple[str, str, str]]:
    return [(f.threat_type, f.severity, f.file_path) for f in findings]


@pytest.mark.parametrize("folder", [".hidden", ".git", "venv", ".venv", "__pycache__", "dist", "build", "a/b/c/d/e"])
def test_a_manifest_is_audited_in_every_folder_but_node_modules(tmp_path: Path, folder: str) -> None:
    _write(tmp_path, f"{folder}/package.json", _POSTINSTALL)
    _write(tmp_path, "node_modules/dep/package.json", _POSTINSTALL)

    assert _summary(audit_skill_directory(tmp_path)) == [("supply_chain", "high", str(Path(folder) / "package.json"))]


@pytest.mark.parametrize("name", [*_HOOK_FILES, "BINDING.GYP", ".PnpmFile.cjs"])
def test_a_hook_file_is_a_high_finding_on_disk_and_in_an_archive(tmp_path: Path, name: str) -> None:
    _write(tmp_path, f"pkg/{name}", "")

    on_disk = audit_skill_directory(tmp_path)
    in_archive = check_lifecycle_scripts({f"pkg/{name}": b""})

    assert [(f.threat_type, f.severity, f.description) for f in on_disk] == [
        ("supply_chain", "high", f"Install-time hook file: {name}")
    ]
    assert [f.file_path for f in on_disk] == [str(Path("pkg") / name)]
    assert [(f.threat_type, f.severity, f.description, f.file_path) for f in in_archive] == [
        ("supply_chain", "high", f"Install-time hook file: {name}", f"pkg/{name}")
    ]


@pytest.mark.parametrize("name", ["binding.gyp.bak", "my.pnpmfile.cjs", ".pnpmfile.txt", "pnpmfile.cjs"])
def test_a_name_that_only_resembles_a_hook_file_is_not_a_finding(tmp_path: Path, name: str) -> None:
    _write(tmp_path, name, "")

    assert audit_skill_directory(tmp_path) == []
    assert check_lifecycle_scripts({name: b""}) == []


def test_a_symlinked_hook_file_is_reported_without_being_followed(tmp_path: Path) -> None:
    try:
        (tmp_path / ".pnpmfile.cjs").symlink_to(tmp_path / "missing.cjs")
    except OSError:
        pytest.skip("symbolic links are not available")

    assert _summary(audit_skill_directory(tmp_path)) == [("supply_chain", "high", ".pnpmfile.cjs")]


def test_a_hook_file_makes_the_directory_scan_untrusted(tmp_path: Path) -> None:
    _write(tmp_path, "SKILL.md", "---\nname: demo\ndescription: A demo skill.\n---\nDo things.\n")
    _write(tmp_path, ".pnpmfile.cjs", "module.exports = {};\n")

    assert compute_scan_summary(scan_skill_directory("demo", tmp_path)).score < 50


def test_an_ordinary_skill_tree_with_a_deep_clean_manifest_stays_clean(tmp_path: Path) -> None:
    _write(tmp_path, "SKILL.md", "---\nname: demo\ndescription: A demo skill.\n---\nDo things.\n")
    _write(tmp_path, "scripts/tools/a/b/c/package.json", '{"name": "clean", "scripts": {"test": "jest"}}')

    assert scan_skill_directory("demo", tmp_path).is_clean


def _clean_manifests(root: Path, count: int) -> None:
    for index in range(count):
        _write(root, f"m{index:03d}/package.json", '{"name": "clean"}')


def test_the_manifest_after_the_cap_is_a_high_finding_instead_of_being_skipped(tmp_path: Path) -> None:
    _clean_manifests(tmp_path, 101)
    _write(tmp_path, "m100/package.json", _POSTINSTALL)

    findings = audit_skill_directory(tmp_path)

    assert _summary(findings) == [("invalid_manifest", "high", str(Path("m100") / "package.json"))]
    assert "more than 100 package.json files" in findings[0].description


def test_every_manifest_up_to_the_cap_is_audited(tmp_path: Path) -> None:
    _clean_manifests(tmp_path, 100)
    _write(tmp_path, "m099/package.json", _POSTINSTALL)

    assert _summary(audit_skill_directory(tmp_path)) == [("supply_chain", "high", str(Path("m099") / "package.json"))]


def _manifest_of_chars(count: int) -> str:
    prefix, suffix = '{"pad": "', '"}'
    return prefix + "a" * (count - len(prefix) - len(suffix)) + suffix


def test_a_manifest_over_the_size_limit_is_unauditable(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", _manifest_of_chars(_LIMIT + 1))

    findings = audit_skill_directory(tmp_path)

    assert _summary(findings) == [("invalid_manifest", "high", "package.json")]
    assert "longer than 1,048,576 characters" in findings[0].description


def test_a_manifest_of_exactly_the_size_limit_is_audited(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", _manifest_of_chars(_LIMIT))

    assert audit_skill_directory(tmp_path) == []


def test_a_manifest_that_is_not_a_regular_file_by_the_time_it_is_read_is_unauditable(tmp_path: Path) -> None:
    from myrm_agent_harness.backends.skills.scanning.package_audit import _audit_manifest_file
    from myrm_agent_harness.backends.skills.scanning.tree_walk import WalkedFile

    fifo = tmp_path / "package.json"
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError):
        pytest.skip("named pipes are not available")

    findings = _audit_manifest_file(WalkedFile(fifo, "package.json", False))

    assert _summary(findings) == [("invalid_manifest", "high", "package.json")]
    assert "not a regular file" in findings[0].description


def test_a_folder_the_audit_cannot_list_is_a_high_finding_and_the_rest_is_still_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "locked").mkdir()
    _write(tmp_path, "open/package.json", _POSTINSTALL)
    real_scandir = os.scandir

    def scandir(path: Path) -> AbstractContextManager[Iterator[os.DirEntry[str]]]:
        if Path(path).name == "locked":
            raise PermissionError(errno.EACCES, "denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)

    findings = audit_skill_directory(tmp_path)

    assert _summary(findings) == [
        ("invalid_manifest", "high", "locked"),
        ("supply_chain", "high", str(Path("open") / "package.json")),
    ]
    assert "a folder cannot be listed" in findings[0].description


def test_a_tree_over_the_entry_budget_is_a_high_finding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tree_walk, "MAX_ENTRIES", 5)
    for index in range(6):
        _write(tmp_path, f"f{index}.txt", "")

    findings = audit_skill_directory(tmp_path)

    assert _summary(findings) == [("invalid_manifest", "high", "")]
    assert "more than 5 entries" in findings[0].description
