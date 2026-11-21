"""Tests that a SKILL.md frontmatter spelling a lone surrogate hands every skill code path text UTF-8 can encode.

[INPUT]
- backends.skills._utils.frontmatter (via api.skills::parse_skill_frontmatter) (POS: the frontmatter parse every skill load and preview goes through)
- agent.skills.market.sources.github::_extract_description_from_skill_md (POS: the market search detail's description)
- agent.skills.market.installers.git_installer::_parse_skill_md_metadata (POS: the name and description of a skill cloned from git)
- agent.skills.market.installers.batch_installer::HermesBatchParser (POS: the metadata of a skill in an uploaded batch archive)
- agent.skills.evolution.execution.dependency::parse_skill_dependencies (POS: the dependency names a skill declares)
- agent.skills.evolution.db.store::SkillStore (POS: the persistence that binds those names into SQL)

[OUTPUT]
- Fails when one of these paths returns, or binds into SQLite, a string holding a lone surrogate, or loses the rest of the text.

[POS]
A double-quoted YAML escape such as ``"\\ud800"`` becomes a lone surrogate under ``yaml.safe_load``; JSON responses,
HTTP bodies and SQLite binds then raise on it. The frontmatter of a skill from a market repository or a local directory is
text the user did not write, so each path that parses it must repair the escape where it parses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from myrm_agent_harness.agent.skills.evolution.core.types import EvolutionType, SkillLineage, SkillRecord
from myrm_agent_harness.agent.skills.evolution.db.store import SkillStore
from myrm_agent_harness.agent.skills.evolution.execution.dependency import parse_skill_dependencies
from myrm_agent_harness.agent.skills.market.installers.batch_installer import HermesBatchParser
from myrm_agent_harness.agent.skills.market.installers.git_installer import _parse_skill_md_metadata
from myrm_agent_harness.agent.skills.market.sources.github import _extract_description_from_skill_md
from myrm_agent_harness.api.skills import parse_skill_frontmatter

REPLACED = "\ufffd"
# The escapes are written out as the YAML text a skill author (or an attacker) would put in the file.
SKILL_MD = '---\nname: "demo\\ud800"\ndescription: "a\\ud800b \\udc80 end"\ncategory: "c\\udfff"\n---\nBody text.\n'
DEPENDENT_MD = '---\nname: dependent\ndescription: "uses a ghost"\ndependencies:\n  - "ghost\\ud800"\n---\nBody text.\n'


def test_the_frontmatter_every_skill_load_reads_is_encodable() -> None:
    parsed = parse_skill_frontmatter(SKILL_MD, "demo")

    assert parsed.name == f"demo{REPLACED}"
    assert parsed.description == f"a{REPLACED}b {REPLACED} end"
    assert parsed.category == f"c{REPLACED}"
    for text in (parsed.name, parsed.description, parsed.category):
        assert text is not None
        text.encode("utf-8")


def test_the_market_detail_description_is_encodable() -> None:
    assert _extract_description_from_skill_md(SKILL_MD) == f"a{REPLACED}b {REPLACED} end"


def test_the_name_and_description_of_a_git_cloned_skill_are_encodable() -> None:
    name, description = _parse_skill_md_metadata(SKILL_MD.encode())

    assert (name, description) == (f"demo{REPLACED}", f"a{REPLACED}b {REPLACED} end")


def test_the_metadata_of_a_batch_archive_skill_is_encodable() -> None:
    name, description, _, body, metadata = HermesBatchParser()._parse_hermes_metadata(SKILL_MD.encode(), "demo")

    assert (name, description, body) == (f"demo{REPLACED}", f"a{REPLACED}b {REPLACED} end", "Body text.")
    assert metadata["category"] == f"c{REPLACED}"


def test_a_declared_dependency_name_is_encodable() -> None:
    assert parse_skill_dependencies(DEPENDENT_MD).skill_deps == (f"ghost{REPLACED}",)


def _record(skill_id: str, content: str) -> SkillRecord:
    return SkillRecord(
        skill_id=skill_id,
        name=skill_id,
        description="desc",
        content=content,
        path=f"skills/{skill_id}.md",
        lineage=SkillLineage(evolution_type=EvolutionType.CAPTURED, version=1),
    )


@pytest.mark.asyncio
async def test_a_skill_declaring_such_a_dependency_is_stored(tmp_path: Path) -> None:
    store = SkillStore(db_path=tmp_path / "skills.db")
    try:
        await store.save_skill(_record("single", DEPENDENT_MD))
        await store.save_skills_batch([_record("first", DEPENDENT_MD), _record("second", DEPENDENT_MD)])

        assert all(store.get_skill(skill_id) is not None for skill_id in ("single", "first", "second"))
        assert store.get_skill_dependencies("single") == []
    finally:
        store.close()
