"""Tests that a name the file system cannot hold is a refused install, not an exception.

[INPUT]
- agent.skills.market.file_install::INVALID_PACKAGE_PATH_MESSAGE, install_skill_files (POS: the install entry and the fixed user sentence under test)

[OUTPUT]
- Fails when a skill name or a path component at the limit is refused, when one over it (or with a NUL or a lone
  surrogate) escapes the install as an exception, loses its error code, shows the user the offending name, leaves
  anything on disk or in the quarantine, or when the skill name still limits how long an installed skill's name can be.

[POS]
Every name of a package is chosen by its publisher. The first write to a name the file system cannot hold raises an
OSError or ValueError, which would surface as an HTTP 500 and, in a plugin import, end the import; the gate in front of
the quarantine turns it into a failed SkillInstallResult before anything is created.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from myrm_agent_harness.agent.skills.market.file_install import INVALID_PACKAGE_PATH_MESSAGE, install_skill_files

INSTALL_LOGGER = "myrm_agent_harness.agent.skills.market.file_install"
SKILL_MD = b"---\nname: demo\ndescription: A demo skill.\n---\nDo things.\n"
PLUGIN_MANIFEST = json.dumps(
    {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "demo-plugin",
        "description": "A demo plugin.",
    }
).encode()

# Paths whose longest component is exactly 255 UTF-8 bytes: the most a package may carry.
AT_THE_LIMIT = {
    "component of 255 bytes": "references/" + "x" * 255,
    "component of 255 bytes in 85 characters": "references/" + "\u20ac" * 85,
    "directory component of 255 bytes": "references/" + "d" * 255 + "/guide.md",
}
# Paths the file system cannot hold: one byte over, or a character no file name can carry.
OVER_THE_LIMIT = {
    "component of 256 bytes": "references/" + "x" * 256,
    "component of 258 bytes in 86 characters": "references/" + "\u20ac" * 86,
    "directory component of 256 bytes": "references/" + "d" * 256 + "/guide.md",
    "NUL in a component": "references/a\x00b",
    "lone surrogate in a component": "references/a\ud800b",
}


def _package_with(path: str) -> dict[str, bytes]:
    return {"SKILL.md": SKILL_MD, path: b"data"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", AT_THE_LIMIT.values(), ids=AT_THE_LIMIT.keys())
async def test_a_path_at_the_limit_installs(tmp_path: Path, path: str) -> None:
    result = await install_skill_files(tmp_path / "skills", "demo-id", "demo", _package_with(path))

    assert (result.success, result.error_code) == (True, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", OVER_THE_LIMIT.values(), ids=OVER_THE_LIMIT.keys())
async def test_a_path_the_file_system_cannot_hold_is_a_refused_install(
    tmp_path: Path, quarantine_root: Path, path: str
) -> None:
    install_root = tmp_path / "skills"

    result = await install_skill_files(install_root, "demo-id", "demo", _package_with(path))

    assert (result.success, result.error_code, result.error) == (
        False,
        "INVALID_PACKAGE_PATH",
        INVALID_PACKAGE_PATH_MESSAGE,
    )
    assert not install_root.exists()
    assert list(quarantine_root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name_length", [200, 229, 255])
async def test_the_longest_skill_name_installs_and_installs_again(tmp_path: Path, name_length: int) -> None:
    install_root = tmp_path / "skills"
    name = "n" * name_length

    first = await install_skill_files(install_root, "demo-id", name, {"SKILL.md": SKILL_MD})
    second = await install_skill_files(install_root, "demo-id", name, {"SKILL.md": SKILL_MD})

    assert (first.success, second.success) == (True, True)
    assert [path.name for path in install_root.iterdir()] == [name]


@pytest.mark.asyncio
async def test_a_plugin_skill_directory_at_the_limit_installs(tmp_path: Path) -> None:
    skill_dir = "s" * 255
    package = {"plugin.json": PLUGIN_MANIFEST, f"skills/{skill_dir}/SKILL.md": SKILL_MD}

    result = await install_skill_files(tmp_path / "skills", "demo-id", "demo", package)

    assert (result.success, result.installed_skills) == (True, [skill_dir])


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["n" * 256, "demo\x00skill"], ids=["256 characters", "NUL"])
async def test_a_skill_name_the_file_system_cannot_hold_is_a_refused_install(
    tmp_path: Path, quarantine_root: Path, name: str
) -> None:
    install_root = tmp_path / "skills"

    result = await install_skill_files(install_root, "demo-id", name, {"SKILL.md": SKILL_MD})

    assert (result.success, result.error_code, result.error) == (
        False,
        "INVALID_PACKAGE_PATH",
        INVALID_PACKAGE_PATH_MESSAGE,
    )
    assert not install_root.exists()
    assert list(quarantine_root.iterdir()) == []


def test_the_fixed_sentence_names_no_path_and_no_number() -> None:
    assert not any(character.isdigit() for character in INVALID_PACKAGE_PATH_MESSAGE)
    assert "/" not in INVALID_PACKAGE_PATH_MESSAGE


@pytest.mark.asyncio
async def test_the_log_names_the_offender_cut_short_and_the_user_never_sees_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    offender = "x" * 300

    with caplog.at_level(logging.WARNING, logger=INSTALL_LOGGER):
        result = await install_skill_files(
            tmp_path / "skills", "demo-id", "demo", _package_with(f"references/{offender}")
        )

    assert "x" * 80 in caplog.text
    assert "x" * 81 not in caplog.text
    assert "xxx" not in result.error
