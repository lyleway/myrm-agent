"""Tests that the install pipeline refuses a skill carrying a file a package manager runs at install.

[INPUT]
- agent.skills.market.file_install::install_skill_files (POS: the single install entry whose first gate is under test)

[OUTPUT]
- Fails when a skill with an install-time hook file passes the lifecycle gate or leaves anything on disk.

[POS]
``check_lifecycle_scripts`` is the gate in front of the quarantine; this pins that the pipeline acts on what it finds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from myrm_agent_harness.agent.skills.market.file_install import install_skill_files

_SKILL_MD = b"---\nname: demo\ndescription: A demo skill.\n---\nDo things.\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_file", ["binding.gyp", ".pnpmfile.cjs", ".npm-extension.mjs"])
async def test_a_skill_with_an_install_time_hook_file_is_refused_before_anything_is_written(
    tmp_path: Path, hook_file: str
) -> None:
    install_root = tmp_path / "skills"

    result = await install_skill_files(install_root, "demo-id", "demo", {"SKILL.md": _SKILL_MD, hook_file: b""})

    assert (result.success, result.error_code) == (False, "LIFECYCLE_SCRIPT_BLOCKED")
    assert hook_file in (result.error or "")
    assert not install_root.exists()
