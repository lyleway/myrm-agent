"""Tests that a name the file system cannot hold is a refused install, not an exception.

[INPUT]
- agent.skills.market.file_install::INVALID_PACKAGE_PATH_MESSAGE, INVALID_SKILL_NAME_MESSAGE, install_skill_files (POS: the install entry and the fixed user sentences under test)

[OUTPUT]
- Fails when a skill name or a path component at the limit is refused, when one over it (or with a NUL or a lone
  surrogate), or a skill name that is no single path segment, escapes the install as an exception, loses its error code,
  shows the user the offending name instead of its fixed sentence, leaves anything on disk or in the quarantine, when a
  refusal echoes a skill name that still holds a lone surrogate (a JSON response cannot carry it), or when the skill name
  still limits how long an installed skill's name can be.

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

from myrm_agent_harness.agent.skills.market.file_install import (
    INVALID_PACKAGE_PATH_MESSAGE,
    INVALID_SKILL_NAME_MESSAGE,
    install_skill_files,
)

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
# Skill names that are no single path segment: a separator, a dot segment, an absolute path or nothing.
NOT_ONE_PATH_SEGMENT = {
    "separator": "a/b",
    "backslash": "a\\b",
    "parent segment": "..",
    "absolute path": "/abs/path",
    "empty": "",
}
# Skill names that are refused and cannot be encoded as UTF-8 (a directory name that was not valid UTF-8 reads back
# as one), with the gate's error code and fixed sentence: the refusal echoes the name, so it has to leave the pipeline
# in a form a JSON response can carry.
UNENCODABLE_SKILL_NAMES = {
    "lone surrogate": ("demo\ud800", "INVALID_PACKAGE_PATH", INVALID_PACKAGE_PATH_MESSAGE),
    "separator and lone surrogate": ("bad/\ud800", "INVALID_SKILL_NAME", INVALID_SKILL_NAME_MESSAGE),
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


@pytest.mark.asyncio
@pytest.mark.parametrize("name", NOT_ONE_PATH_SEGMENT.values(), ids=NOT_ONE_PATH_SEGMENT.keys())
async def test_a_skill_name_that_is_no_single_path_segment_is_a_refused_install(
    tmp_path: Path, quarantine_root: Path, name: str
) -> None:
    install_root = tmp_path / "skills"

    result = await install_skill_files(install_root, "demo-id", name, {"SKILL.md": SKILL_MD})

    assert (result.success, result.error_code, result.error) == (
        False,
        "INVALID_SKILL_NAME",
        INVALID_SKILL_NAME_MESSAGE,
    )
    assert not install_root.exists()
    assert list(quarantine_root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, error_code, message", UNENCODABLE_SKILL_NAMES.values(), ids=UNENCODABLE_SKILL_NAMES.keys()
)
async def test_a_refusal_echoes_the_skill_name_in_a_form_a_json_response_can_carry(
    tmp_path: Path, name: str, error_code: str, message: str
) -> None:
    result = await install_skill_files(tmp_path / "skills", "demo-id", name, {"SKILL.md": SKILL_MD})

    assert (result.success, result.error_code, result.error) == (False, error_code, message)
    assert result.skill_name == name.replace("\ud800", "\ufffd")


@pytest.mark.parametrize(
    "message", [INVALID_PACKAGE_PATH_MESSAGE, INVALID_SKILL_NAME_MESSAGE], ids=["package path", "skill name"]
)
def test_a_fixed_sentence_names_no_path_and_no_number(message: str) -> None:
    assert not any(character.isdigit() for character in message)
    assert "/" not in message


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


@pytest.mark.asyncio
async def test_the_log_names_a_skill_name_that_is_no_path_segment_cut_short_and_the_user_never_sees_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    offender = "a/" + "y" * 300

    with caplog.at_level(logging.WARNING, logger=INSTALL_LOGGER):
        result = await install_skill_files(tmp_path / "skills", "demo-id", offender, {"SKILL.md": SKILL_MD})

    assert "a/yyy" in caplog.text
    assert "y" * 100 not in caplog.text
    assert "yyy" not in result.error
