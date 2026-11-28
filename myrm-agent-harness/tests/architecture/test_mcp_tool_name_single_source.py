"""The name the model sees for an MCP tool is composed in ``toolkits/mcp/config.py::mcp_tool_name`` and nowhere else.

[INPUT]
- src/myrm_agent_harness/**/*.py: every call of ``sanitize_mcp_name_component`` and ``clamp_tool_name``

[OUTPUT]
- Fails when a module other than ``toolkits/mcp/config.py`` sanitizes a tool segment or clamps a name by itself: a second
  copy of the composition registers a tool under one name and looks it up under another, and the 64-character limit holds
  for one of the two paths only. The scanner reads calls by plain name and by attribute (``config.clamp_tool_name(...)``);
  a function bound to another name first (``alias = clamp_tool_name``) is not seen.

[POS]
tests/architecture/test_mcp_tool_name_single_source.py - guard for ``mcp_tool_name``: registration
(``tool_processing.prefix_tool_names``) and bare-name lookup (``session_actor_tools.resolve_actor_tool``) both call it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

_SRC = Path(__file__).resolve().parents[2] / "src" / "myrm_agent_harness"
_COMPOSER = "toolkits/mcp/config.py"
_COMPOSITION_STEPS = frozenset({"sanitize_mcp_name_component", "clamp_tool_name"})


def composition_calls(source: str) -> Iterator[tuple[int, str]]:
    """``(line, function)`` for each call in *source* that sanitizes a tool segment or clamps a name."""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name in _COMPOSITION_STEPS:
            yield node.lineno, name


def _calls_by_module() -> Iterator[tuple[str, int, str]]:
    for path in sorted(_SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(step in text for step in _COMPOSITION_STEPS):
            relative = path.relative_to(_SRC).as_posix()
            for line, name in composition_calls(text):
                yield relative, line, name


def test_only_the_config_module_composes_a_tool_name() -> None:
    offenders = [f"{relative}:{line}: {name}" for relative, line, name in _calls_by_module() if relative != _COMPOSER]

    assert not offenders, (
        "build the name with myrm_agent_harness.toolkits.mcp.config.mcp_tool_name so registration and lookup agree on "
        "every name and none exceeds the 64-character budget:\n" + "\n".join(offenders)
    )


def test_the_composer_still_runs_both_steps() -> None:
    steps = {name for relative, _, name in _calls_by_module() if relative == _COMPOSER}

    assert steps == _COMPOSITION_STEPS


def test_the_composer_still_defines_the_composition() -> None:
    tree = ast.parse((_SRC / _COMPOSER).read_text(encoding="utf-8"))

    assert any(isinstance(node, ast.FunctionDef) and node.name == "mcp_tool_name" for node in ast.walk(tree))


class TestTheScannerSeesWhatItShould:
    @pytest.mark.parametrize(
        ("source", "name"),
        [
            ("segment = sanitize_mcp_name_component(tool.name)", "sanitize_mcp_name_component"),
            ("segment = config.sanitize_mcp_name_component(tool.name)", "sanitize_mcp_name_component"),
            ("name = clamp_tool_name(prefix, segment)", "clamp_tool_name"),
            ("name = tool_name.clamp_tool_name(prefix, segment)", "clamp_tool_name"),
        ],
    )
    def test_each_way_of_calling_a_step_is_found(self, source: str, name: str) -> None:
        assert list(composition_calls(source)) == [(1, name)]

    @pytest.mark.parametrize(
        "source",
        [
            "from .config import sanitize_mcp_name_component",
            "steps = [sanitize_mcp_name_component, clamp_tool_name]",
            "name = mcp_tool_name(server, tool.name)",
            "text = sanitize_other(tool.name)",
        ],
    )
    def test_naming_a_step_without_calling_it_is_not_a_composition(self, source: str) -> None:
        assert list(composition_calls(source)) == []
