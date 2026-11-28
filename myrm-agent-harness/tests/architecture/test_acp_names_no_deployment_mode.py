"""The ACP toolkit does not know how the host is deployed: whether an agent may run at all is the host's decision.

[INPUT]
- src/myrm_agent_harness/toolkits/acp/**/*.py: every string, name, attribute, parameter and import that spells a deployment mode

[OUTPUT]
- Fails when a module of the ACP toolkit reads or names the deployment mode (the ``DEPLOY_MODE`` environment variable,
  anything called ``deploy_mode`` or ``DeployMode``). The one library serves a person's own machine, a desktop and a
  hosted sandbox; a runtime that branched on the mode would run external agents differently for a reason its caller
  can neither see nor override, so the host decides (what to register, which login to offer) and the toolkit does what
  it is given. Comments and docstrings may use the words; a key assembled out of parts (``"DEPLOY_" + "MODE"``) is not
  seen.

[POS]
tests/architecture/test_acp_names_no_deployment_mode.py - guard of the line between the host's deployment policy and
the ACP toolkit, which stays free of it so a standalone user of the harness gets no hidden mode switch.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

_SRC = Path(__file__).resolve().parents[2] / "src" / "myrm_agent_harness"
_ACP = _SRC / "toolkits" / "acp"
_SPELLINGS = ("deploy_mode", "deploymode")
# The node fields that carry a name: variables, attributes, parameters, keywords, definitions, imports and aliases.
_NAME_FIELDS = ("id", "attr", "arg", "name", "asname", "module")
_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _spells_a_mode(text: str) -> bool:
    lowered = text.lower()
    return any(spelling in lowered for spelling in _SPELLINGS)


def _docstrings(tree: ast.AST) -> set[int]:
    """The ``id`` of every string node that is a docstring: prose about the code, not code."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_OWNERS) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def mode_mentions(source: str) -> list[tuple[int, str]]:
    """``(line, text)`` for each name or string in *source* that spells a deployment mode, outside docstrings."""
    tree = ast.parse(source)
    docstrings = _docstrings(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and _spells_a_mode(node.value):
                found.append((node.lineno, node.value))
            continue
        for field in _NAME_FIELDS:
            name = getattr(node, field, None)
            if isinstance(name, str) and _spells_a_mode(name):
                found.append((getattr(node, "lineno", 0), name))
    return sorted(found)


def _acp_modules() -> Iterator[tuple[str, str]]:
    for path in sorted(_ACP.rglob("*.py")):
        yield path.relative_to(_SRC).as_posix(), path.read_text(encoding="utf-8")


def test_no_module_of_the_acp_toolkit_names_a_deployment_mode() -> None:
    offenders = [
        f"{relative}:{line}: {text}" for relative, source in _acp_modules() for line, text in mode_mentions(source)
    ]

    assert not offenders, (
        "the host decides what a deployment allows (what to register, which login to offer); the ACP toolkit does "
        "what it is given:\n" + "\n".join(offenders)
    )


def test_the_scan_covers_the_runtimes_and_the_auth_package() -> None:
    """A guard that scans nothing passes forever: the directories where such a branch would be written are in it."""
    relatives = {relative for relative, _ in _acp_modules()}

    assert "toolkits/acp/runtime/pool.py" in relatives
    assert "toolkits/acp/runtime/codex/runtime.py" in relatives
    assert any(relative.startswith("toolkits/acp/auth/") for relative in relatives)


class TestTheScannerSeesWhatItShould:
    @pytest.mark.parametrize(
        ("source", "text"),
        [
            ('mode = os.environ.get("DEPLOY_MODE")', "DEPLOY_MODE"),
            ('mode = os.getenv("deploy_mode", "local")', "deploy_mode"),
            ('mode = os.environ["Deploy_Mode"]', "Deploy_Mode"),
            ("mode = settings.deploy_mode", "deploy_mode"),
            ("def run(deploy_mode: str) -> None: ...", "deploy_mode"),
            ("run(deploy_mode=mode)", "deploy_mode"),
            ("from app.config import DeployMode", "DeployMode"),
            ("import app.deploy_mode as modes", "app.deploy_mode"),
            ('print(f"mode={deploy_mode}")', "deploy_mode"),
            ('message = f"deploy_mode={mode}"', "deploy_mode="),
        ],
    )
    def test_each_way_of_reading_or_naming_the_mode_is_found(self, source: str, text: str) -> None:
        assert [found for _, found in mode_mentions(source)] == [text]

    @pytest.mark.parametrize(
        "source",
        [
            '"""A hosted sandbox sets DEPLOY_MODE; this module never looks."""',
            'def run() -> None:\n    """Nothing here reads deploy_mode."""\n',
            "class Pool:\n    '''Not a DeployMode switch.'''\n",
            "# the host reads DEPLOY_MODE, not this module\nvalue = 1",
            'home = os.environ.get("HOME")',
            "deploy(mode)",
            'label = "deployed in a mode"',
        ],
    )
    def test_prose_and_unrelated_names_are_not_a_mention(self, source: str) -> None:
        assert mode_mentions(source) == []
