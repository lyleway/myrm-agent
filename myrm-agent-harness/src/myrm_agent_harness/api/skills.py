"""Stable skill metadata utilities for product skill management.

[INPUT]
- myrm_agent_harness.backends.skills._runtime (POS: skill metadata builder and content hash)
- myrm_agent_harness.backends.skills._utils (POS: SKILL.md frontmatter parser)
- myrm_agent_harness.backends.skills.evolution_lock (POS: evolution_locked frontmatter writer)
- myrm_agent_harness.utils.clean_yaml (POS: safe YAML loader that repairs lone surrogates)

[OUTPUT]
- SkillMetadataError, parse_skill_frontmatter, update_frontmatter_evolution_lock,
  compute_content_hash, read_skill_md, build_skill_metadata, load_clean_yaml

[POS]
Public re-export facade. Product skill CRUD/sync code imports here instead of
private ``backends.skills._*`` modules.
"""

from __future__ import annotations

from myrm_agent_harness.backends.skills._runtime import (
    build_skill_metadata,
    compute_content_hash,
    read_skill_md,
)
from myrm_agent_harness.backends.skills._utils import (
    SkillMetadataError,
    parse_skill_frontmatter,
)
from myrm_agent_harness.backends.skills.evolution_lock import update_frontmatter_evolution_lock
from myrm_agent_harness.backends.skills.market_protocols import SkillInstallResult
from myrm_agent_harness.utils.clean_yaml import load_clean_yaml

__all__ = [
    "SkillInstallResult",
    "SkillMetadataError",
    "build_skill_metadata",
    "compute_content_hash",
    "load_clean_yaml",
    "parse_skill_frontmatter",
    "read_skill_md",
    "update_frontmatter_evolution_lock",
]
