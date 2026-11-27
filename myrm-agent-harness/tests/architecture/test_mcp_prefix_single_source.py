"""The ``mcp__{server}__`` tool-name prefix is derived in ``toolkits/mcp/config.py`` and nowhere else in the harness.

[INPUT]
- src/myrm_agent_harness/**/*.py: every f-string, ``+``, ``%`` and ``.format`` expression whose text starts with the literal ``mcp__``

[OUTPUT]
- Fails when a module other than ``toolkits/mcp/config.py`` assembles an ``mcp__…`` name out of parts: a second copy of
  the rule resolves, evicts or approves a tool under another server segment than the one the tool was registered with,
  and no test of either side notices. The agent runtimes under ``toolkits/acp/runtime/`` are exempt: they name tools of an
  external agent's own MCP servers, which never pass through the server identity rule. The scanner reads expressions whose
  first text is the literal ``mcp__``; a prefix assembled from a constant bound elsewhere (``f"{PREFIX}{server}__"``) or
  with ``"__".join(...)`` is not seen.

[POS]
tests/architecture/test_mcp_prefix_single_source.py - guard for ``toolkits/mcp/config.py::mcp_tool_prefix``: the server
segment comes from ``mcp_server_component`` and the prefix is derived in that one function.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

_SRC = Path(__file__).resolve().parents[2] / "src" / "myrm_agent_harness"
_PREFIX_SOURCE = "toolkits/mcp/config.py"
_EXTERNAL_RUNTIMES = "toolkits/acp/runtime/"
_LITERAL = "mcp__"


def _starts_with_literal(node: ast.expr) -> bool:
    """Whether *node* is a text whose first characters are the literal ``mcp__``."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and node.value.startswith(_LITERAL)
    if isinstance(node, ast.JoinedStr):
        return bool(node.values) and _starts_with_literal(node.values[0])
    return False


def _build_form(node: ast.expr) -> str | None:
    """How *node* assembles a name starting with ``mcp__`` out of parts, or ``None`` when it does not."""
    if isinstance(node, ast.JoinedStr):
        has_placeholder = any(isinstance(part, ast.FormattedValue) for part in node.values)
        return "f-string" if has_placeholder and _starts_with_literal(node) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return "concatenation" if _starts_with_literal(node.left) else None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return "format" if _starts_with_literal(node.func.value) else None
    return None


def prefix_builds(source: str) -> Iterator[tuple[int, str]]:
    """``(line, form)`` for each expression in *source* that assembles a name starting with ``mcp__`` out of parts."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.expr) and (form := _build_form(node)) is not None:
            yield node.lineno, form


def _builds_by_module() -> Iterator[tuple[str, int, str]]:
    for path in sorted(_SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if _LITERAL in text:
            relative = path.relative_to(_SRC).as_posix()
            for line, form in prefix_builds(text):
                yield relative, line, form


def test_only_the_config_module_and_the_external_runtimes_assemble_an_mcp_prefix() -> None:
    offenders = [
        f"{relative}:{line}: {form}"
        for relative, line, form in _builds_by_module()
        if relative != _PREFIX_SOURCE and not relative.startswith(_EXTERNAL_RUNTIMES)
    ]

    assert not offenders, (
        "derive the prefix with myrm_agent_harness.toolkits.mcp.config.mcp_tool_prefix so every layer agrees on the "
        "server segment:\n" + "\n".join(offenders)
    )


def test_the_prefix_source_still_defines_the_derivation() -> None:
    tree = ast.parse((_SRC / _PREFIX_SOURCE).read_text(encoding="utf-8"))

    assert any(isinstance(node, ast.FunctionDef) and node.name == "mcp_tool_prefix" for node in ast.walk(tree))


def test_the_exempt_runtimes_still_assemble_mcp_names() -> None:
    """An exemption that covers nothing any more is dropped, so the allowed set cannot grow stale."""
    assert any(relative.startswith(_EXTERNAL_RUNTIMES) for relative, _, _ in _builds_by_module())


class TestTheScannerSeesWhatItShould:
    @pytest.mark.parametrize(
        ("source", "form"),
        [
            ('name = f"mcp__{server}__{tool}"', "f-string"),
            ('name = "mcp__" + server + "__"', "concatenation"),
            ('name = "mcp__%s__" % server', "concatenation"),
            ('name = "mcp__{}__".format(server)', "format"),
        ],
    )
    def test_each_way_of_assembling_a_prefix_is_found(self, source: str, form: str) -> None:
        assert list(prefix_builds(source)) == [(1, form)]

    @pytest.mark.parametrize(
        "source",
        [
            'is_mcp = name.startswith("mcp__")',
            'rest = name[len("mcp__") :]',
            'MARKER = "mcp__"',
            'text = f"mcp__"',
            'text = f"{prefix}{tool}"',
            'text = f"{server}mcp__"',
            'doc = "the mcp__{server}__{tool} convention"',
        ],
    )
    def test_reading_a_prefix_or_a_name_that_does_not_start_with_it_is_not_a_build(self, source: str) -> None:
        assert list(prefix_builds(source)) == []
