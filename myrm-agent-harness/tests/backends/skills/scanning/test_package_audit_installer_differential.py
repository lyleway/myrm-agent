"""Differential tests: what the lifecycle-script audit reports for a skill against what installed package managers run.

[INPUT]
- backends.skills.scanning.package_audit::audit_skill_directory (POS: the disk gate under test)
- tests.support.symlinks::make_symlink (POS: builds the links a hostile skill ships)
- node, and any of npm, pnpm, yarn and bun on PATH (POS: the installers the audit stands in for; a manager that is not
  installed, or cannot run the script of a package here, is skipped)

[OUTPUT]
- Fails when a skill whose package an installed package manager runs a lifecycle script of reads as clean to the audit:
  the package in a plain workspace folder, behind a symbolic link a workspace glob follows, behind one a workspace entry
  names, or behind one a ``file:`` dependency follows.

[POS]
``test_tree_walk.py`` and ``test_package_audit_tree.py`` pin the audit's rules against the author's idea of an
installer; this asks the installers. Which of these links a package manager follows differs between managers and
versions, so a construct a manager does not run is skipped for it, since there is then nothing to compare.
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

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="the payload is a node script")

_MARKER_ENV = "AUDIT_DIFFERENTIAL_MARKER"
# The script leaves a marker file, so "an installer ran it" is an observation and not a guess.
_PAYLOAD = json.dumps(
    {
        "name": "payload",
        "version": "1.0.0",
        "scripts": {"postinstall": f"node -e \"require('fs').writeFileSync(process.env.{_MARKER_ENV}, 'ran')\""},
    }
)

# What each manager is asked offline; bun has no offline switch, and the skills hold no registry dependency to fetch.
_INSTALL = {
    "npm": ("install", "--offline", "--ignore-scripts=false", "--no-audit", "--no-fund"),
    "pnpm": ("install", "--offline"),
    "yarn": ("install", "--offline"),
    "bun": ("install",),
}

# Where the package is, the finding the audit owes for it, and the file the finding names.
_Construct = Callable[[Path, Path], tuple[str, str]]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manifest(**fields: object) -> str:
    # yarn takes workspaces from a private package only.
    return json.dumps({"name": "skill", "version": "1.0.0", "private": True, **fields})


def _workspace_root(skill: Path, *entries: str) -> None:
    """The skill declares *entries* as workspaces, in the file each manager reads (pnpm reads its own)."""
    _write(skill / "package.json", _manifest(workspaces=list(entries)))
    _write(skill / "pnpm-workspace.yaml", "packages:\n" + "".join(f"  - '{entry}'\n" for entry in entries))


def _workspace_folder_in_the_skill(skill: Path, outside: Path) -> tuple[str, str]:
    _workspace_root(skill, "ws/*")
    _write(skill / "ws" / "pkg" / "package.json", _PAYLOAD)
    return "supply_chain", str(Path("ws") / "pkg" / "package.json")


def _workspace_folder_behind_a_link(skill: Path, outside: Path) -> tuple[str, str]:
    _workspace_root(skill, "ws/*")
    _write(outside / "pkg" / "package.json", _PAYLOAD)
    make_symlink(skill / "ws", outside)
    return "invalid_manifest", "ws"


def _workspace_entry_that_is_a_link(skill: Path, outside: Path) -> tuple[str, str]:
    _workspace_root(skill, "lnk")
    _write(outside / "pkg" / "package.json", _PAYLOAD)
    make_symlink(skill / "lnk", outside / "pkg")
    return "invalid_manifest", "lnk"


def _file_dependency_behind_a_link(skill: Path, outside: Path) -> tuple[str, str]:
    _write(skill / "package.json", _manifest(dependencies={"payload": "file:./lnk"}))
    _write(outside / "pkg" / "package.json", _PAYLOAD)
    make_symlink(skill / "lnk", outside / "pkg")
    return "invalid_manifest", "lnk"


def _runs_the_payload(command: list[str], skill: Path, home: Path) -> bool:
    """Run the installer *command* in *skill* with a home of its own, and say whether the payload's script ran."""
    home.mkdir()
    marker = home / "marker"
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        # A corepack shim (how yarn often comes) keeps the manager it fetched under the real home and, offline, finds
        # it only there; it must never fetch one into the home above.
        "COREPACK_HOME": os.environ.get("COREPACK_HOME", str(Path.home() / ".cache" / "node" / "corepack")),
        "COREPACK_ENABLE_NETWORK": "0",
        "npm_config_update_notifier": "false",
        _MARKER_ENV: str(marker),
    }
    subprocess.run(command, cwd=skill, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=120, check=False)
    return marker.exists()


@pytest.fixture(scope="module", params=list(_INSTALL))
def installer(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    """The install command of one package manager, or a skip where it is not installed or runs no script here."""
    manager: str = request.param
    executable = shutil.which(manager)
    if executable is None:
        pytest.skip(f"{manager} is not installed")
    command = [executable, *_INSTALL[manager]]
    # The skill's own package is the script every installer runs; if it does not run, no construct can tell anything.
    scratch = tmp_path_factory.mktemp(f"{manager}-control")
    _write(scratch / "skill" / "package.json", _PAYLOAD)
    if not _runs_the_payload(command, scratch / "skill", scratch / "home"):
        pytest.skip(f"{manager} does not run the script of a package here")
    return command


@pytest.mark.parametrize(
    "construct",
    [
        _workspace_folder_in_the_skill,
        _workspace_folder_behind_a_link,
        _workspace_entry_that_is_a_link,
        _file_dependency_behind_a_link,
    ],
)
def test_a_package_an_installer_runs_a_script_of_never_reads_as_clean_to_the_audit(
    tmp_path: Path, installer: list[str], construct: _Construct
) -> None:
    skill = tmp_path / "skill"
    threat_type, file_path = construct(skill, tmp_path / "outside")
    findings = audit_skill_directory(skill)

    if not _runs_the_payload(installer, skill, tmp_path / "home"):
        pytest.skip("this installer does not run the package of the construct, so there is nothing to compare")

    assert [(f.threat_type, f.file_path) for f in findings if f.severity == "high"] == [(threat_type, file_path)]
