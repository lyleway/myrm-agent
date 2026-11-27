"""Architecture guard: Unicode normalization goes through ``utils.unicode_normalize``.

``unicodedata.normalize`` sorts every run of combining marks with a quadratic sort, so a text of a few tens of thousands
of stacked marks holds the interpreter for seconds. ``utils.unicode_normalize.normalize`` is the same call on the text in
Stream-Safe Text Format; this test refuses any other use of ``unicodedata.normalize`` in the harness.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_HARNESS_SRC = Path(__file__).resolve().parent.parent.parent / "src" / "myrm_agent_harness"
_OWNER = "utils/unicode_normalize.py"


def _direct_uses(source: str) -> list[int]:
    """Lines that name ``unicodedata.normalize``: through the module, through an alias or imported by name."""
    tree = ast.parse(source)
    modules: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names if alias.name == "unicodedata")
        elif isinstance(node, ast.ImportFrom) and node.module == "unicodedata":
            functions.update(alias.asname or alias.name for alias in node.names if alias.name == "normalize")
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr == "normalize" and isinstance(node.value, ast.Name) and node.value.id in modules:
                lines.append(node.lineno)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in functions:
            lines.append(node.lineno)
    return lines


@pytest.mark.architecture
def test_no_module_calls_unicodedata_normalize_directly() -> None:
    offenders = {
        relative: lines
        for py_file in sorted(_HARNESS_SRC.rglob("*.py"))
        if (relative := py_file.relative_to(_HARNESS_SRC).as_posix()) != _OWNER
        if (lines := _direct_uses(py_file.read_text(encoding="utf-8")))
    }

    assert not offenders, (
        "unicodedata.normalize sorts a run of combining marks in quadratic time; call "
        f"utils.unicode_normalize.normalize(form, text) instead: {offenders}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "import unicodedata\nunicodedata.normalize('NFC', text)\n",
        "import unicodedata as ud\nud.normalize('NFKC', text)\n",
        "from unicodedata import normalize\nnormalize('NFD', text)\n",
        "from unicodedata import normalize as fold\nfold('NFKD', text)\n",
        "import unicodedata\nlist(map(unicodedata.normalize, forms, texts))\n",
    ],
    ids=["module", "module-alias", "imported-by-name", "imported-under-another-name", "passed-as-a-value"],
)
def test_guard_flags_a_direct_use(source: str) -> None:
    assert _direct_uses(source)


@pytest.mark.parametrize(
    "source",
    [
        "import unicodedata\nunicodedata.category(char)\n",
        "from unicodedata import east_asian_width\neast_asian_width(char)\n",
        "from myrm_agent_harness.utils.unicode_normalize import normalize\nnormalize('NFKC', text)\n",
        "other.normalize(text)\n",
    ],
    ids=["other-function", "other-function-by-name", "the-shared-entry", "another-module"],
)
def test_guard_leaves_everything_else_alone(source: str) -> None:
    assert _direct_uses(source) == []
