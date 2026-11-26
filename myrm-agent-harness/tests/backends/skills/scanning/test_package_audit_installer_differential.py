"""Differential tests: what the lifecycle-script audit reports for a skill against what a real ``npm install`` runs.

[INPUT]
- backends.skills.scanning.package_audit::audit_skill_directory (POS: the disk gate under test)
- tests.support.symlinks::make_symlink (POS: builds the links a hostile skill ships)
- npm and node on PATH (POS: the installer the audit stands in for; the tests are skipped without them)

[OUTPUT]
- Fails when a skill whose package a real ``npm install`` runs a lifecycle script of reads as clean to the audit: the
  package in a plain workspace folder, behind a symbolic link a workspace glob follows, or behind one a ``file:``
  dependency follows.

[POS]
``test_tree_walk.py`` and ``test_package_audit_tree.py`` pin the audit's rules against the author's idea of an
installer; this asks the installer. A construct the installed npm does not run is skipped, since there is then nothing
to compare.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from myrm_agent_harness.backends.skills.scanning.package_audit import audit_skill_directory
from tests.support.symlinks import make_symlink

_NPM = shutil.which("npm") or ""

pytestmark = pytest.mark.skipif(not _NPM or not shutil.which("node"), reason="differential test needs npm and node")

_MARKER_ENV = "AUDIT_DIFFERENTIAL_MARKER"
# The script leaves a marker file, so "npm ran it" is an observation and not a guess.
_PAYLOAD = json.dumps(
    {
        "name": "payload",
        "version": "1.0.0",
        "scripts": {"postinstall": f"node -e \"require('fs').writeFileSync(process.env.{_MARKER_ENV}, 'ran')\""},
    }
)

# Where the package is, the finding the audit owes for it, and the file the finding names.
_Construct = Callable[[Path, Path], tuple[str, str]]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manifest(**fields: object) -> str:
    return json.dumps({"name": "skill", "version": "1.0.0", **fields})


def _workspace_folder_in_the_skill(skill: Path, outside: Path) -> tuple[str, str]:
    _write(skill / "package.json", _manifest(workspaces=["ws/*"]))
    _write(skill / "ws" / "pkg" / "package.json", _PAYLOAD)
    return "supply_chain", str(Path("ws") / "pkg" / "package.json")


def _workspace_folder_behind_a_link(skill: Path, outside: Path) -> tuple[str, str]:
    _write(skill / "package.json", _manifest(workspaces=["ws/*"]))
    _write(outside / "pkg" / "package.json", _PAYLOAD)
    make_symlink(skill / "ws", outside)
    return "invalid_manifest", "ws"


def _file_dependency_behind_a_link(skill: Path, outside: Path) -> tuple[str, str]:
    _write(skill / "package.json", _manifest(dependencies={"payload": "file:./lnk"}))
    _write(outside / "pkg" / "package.json", _PAYLOAD)
    make_symlink(skill / "lnk", outside / "pkg")
    return "invalid_manifest", "lnk"


def _npm_runs_the_payload(skill: Path, home: Path) -> bool:
    """Run ``npm install`` offline in *skill* with a home of its own, and say whether the payload's script ran."""
    home.mkdir()
    marker = home / "marker"
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "npm_config_update_notifier": "false",
        _MARKER_ENV: str(marker),
    }
    subprocess.run(
        [_NPM, "install", "--offline", "--ignore-scripts=false", "--no-audit", "--no-fund"],
        cwd=skill,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
        check=False,
    )
    return marker.exists()


@pytest.mark.parametrize(
    "construct", [_workspace_folder_in_the_skill, _workspace_folder_behind_a_link, _file_dependency_behind_a_link]
)
def test_a_package_npm_runs_a_script_of_never_reads_as_clean_to_the_audit(
    tmp_path: Path, construct: _Construct
) -> None:
    skill = tmp_path / "skill"
    threat_type, file_path = construct(skill, tmp_path / "outside")
    findings = audit_skill_directory(skill)

    if not _npm_runs_the_payload(skill, tmp_path / "home"):
        pytest.skip("this npm does not run the package of the construct, so there is nothing to compare")

    assert [(f.threat_type, f.file_path) for f in findings if f.severity == "high"] == [(threat_type, file_path)]
