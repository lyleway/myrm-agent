"""Architecture test: the security settings page names only what the policy engine can judge.

[INPUT]
- myrm-agent-frontend/src/components/features/settings/sections/system/securityPolicyUtils.ts (the capability surfaces,
  the tools each one lists and the permission names the custom-rule editor offers)
- myrm_agent_harness.core.security (the vocabulary the engine judges calls under)

[POS]
The frontend and the backend each keep a list of permission names and nothing compared them: a switch that listed
``remote_command_exec`` stayed on the page although no call is ever judged under that name. A name the page shows that
the engine does not know promises a protection that does not exist, so every such name fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

from myrm_agent_harness.core.security.tool_registry import BUILTIN_TOOL_NAMES, TOOL_PERMISSION_MAP
from myrm_agent_harness.core.security.types import (
    CAPABILITY_SURFACE_PERMISSIONS,
    DEFAULT_RULESET,
    CapabilitySurface,
    SecurityConfig,
)

_SETTINGS_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "myrm-agent-frontend/src/components/features/settings/sections/system/securityPolicyUtils.ts"
)


def _array_after(source: str, declaration: str) -> str:
    """The text of the array literal that ``declaration`` opens (up to its closing ``] as const`` / ``];``)."""
    start = source.index(declaration)
    end = source.index("] as const", start)
    return source[start:end]


def _quoted(text: str) -> list[str]:
    return re.findall(r"'([a-z_]+)'", text)


def _frontend_surfaces() -> dict[str, list[str]]:
    """Each capability surface of the page and the tool names it lists."""
    block = _array_after(_SETTINGS_SOURCE.read_text(encoding="utf-8"), "export const CAPABILITY_SURFACES")
    surfaces = {
        match.group(1): _quoted(match.group(2))
        for match in re.finditer(r"key: '([a-z_]+)',.*?tools: \[(.*?)\]", block, re.DOTALL)
    }
    assert surfaces, "no capability surface parsed from securityPolicyUtils.ts: the extraction needs updating"
    return surfaces


def _frontend_known_permissions() -> list[str]:
    block = _array_after(_SETTINGS_SOURCE.read_text(encoding="utf-8"), "export const KNOWN_PERMISSIONS")
    names = _quoted(block)
    assert names, "no permission parsed from securityPolicyUtils.ts: the extraction needs updating"
    return names


def _engine_names() -> frozenset[str]:
    """Every name a call can be judged under or a rule can be written for: permission types, tool names, rule keys."""
    names: set[str] = {*TOOL_PERMISSION_MAP, *TOOL_PERMISSION_MAP.values(), *BUILTIN_TOOL_NAMES}
    for permissions in CAPABILITY_SURFACE_PERMISSIONS.values():
        names.update(permissions)
    names.update(rule.permission for rule in DEFAULT_RULESET)
    for profile in (
        SecurityConfig.full_access(),
        SecurityConfig.workspace(allowed_roots=()),
        SecurityConfig.readonly(),
        SecurityConfig.remote_exposed(),
    ):
        names.update(rule.permission for rule in profile.ruleset)
    names.discard("*")
    return frozenset(names)


def test_the_page_offers_exactly_the_capability_surfaces_the_engine_has() -> None:
    assert set(_frontend_surfaces()) == {surface.value for surface in CapabilitySurface}


def test_every_tool_a_capability_surface_lists_is_a_name_the_engine_judges() -> None:
    known = _engine_names()

    unknown = {surface: sorted(set(tools) - known) for surface, tools in _frontend_surfaces().items() if set(tools) - known}

    assert not unknown, f"surfaces list names no call is judged under (they promise a protection that does not exist): {unknown}"


def test_every_permission_the_rule_editor_offers_is_a_name_the_engine_judges() -> None:
    assert not set(_frontend_known_permissions()) - _engine_names()


def test_the_remote_tools_surface_lists_the_permissions_the_engine_fans_it_out_to() -> None:
    """The switch's own chips must match what it governs: one it lists and the engine ignores is a lie, one it hides a surprise."""
    listed = set(_frontend_surfaces()["remote_tools"])

    assert listed == set(CAPABILITY_SURFACE_PERMISSIONS[CapabilitySurface.REMOTE_TOOLS])
