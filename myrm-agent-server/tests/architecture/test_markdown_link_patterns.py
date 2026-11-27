"""A pattern shaped like ``[label](target)`` is either bounded, run on masked text, or accounted for here.

A pattern that reads a label up to ``]`` and a target up to ``)`` starts over from every ``[`` that never closes, so a
line of 20,000 ``[`` holds the interpreter for a second, and the lazy forms (``.+?``) take seconds on 4,000 characters
of ``[a](``. ``utils.markdown_links`` reads the same links by position in time proportional to the text. A new pattern of
this shape must be a conscious decision: read the links with that module, or list the file below with the reason its
pattern cannot start over from many positions.

The scan reads ``re.<function>(...)`` calls whose pattern literal holds ``\\]\\(``; a pattern that spells the join
differently (``\\]\\s*\\(``) or builds it at run time is not covered.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_TREES = (_ROOT / "myrm-agent-harness" / "src", _ROOT / "myrm-agent-server" / "app")
_LINK_JOIN = "\\]\\("
_RE_FUNCTIONS = frozenset({"compile", "search", "match", "fullmatch", "sub", "subn", "findall", "finditer", "split"})

# file -> (number of patterns, why none of them can start over from many positions)
_SAFE_BY_CONSTRUCTION: dict[str, tuple[int, str]] = {
    "myrm-agent-harness/src/myrm_agent_harness/utils/markdown_links.py": (
        1,
        "the nested-label pattern runs on text whose openers without a closing `](` were replaced, so no start fails slowly",
    ),
    "myrm-agent-server/app/channels/providers/feishu/cards/text.py": (
        1,
        "the inline pattern runs on text masked by `mask_unclosable_openers`, the same construction",
    ),
    "myrm-agent-harness/src/myrm_agent_harness/toolkits/wiki/core/link_extraction.py": (
        1,
        "label and target are each bounded at MAX_LINK_PART characters",
    ),
    "myrm-agent-harness/src/myrm_agent_harness/toolkits/wiki/retrieval/graph_store.py": (
        1,
        "the label and the two stretches of the target around the page's own address are each bounded at MAX_LINK_PART",
    ),
    "myrm-agent-harness/src/myrm_agent_harness/toolkits/web_fetch/_url_gate.py": (
        1,
        "anchored at both ends and applied to one value, so there is a single start",
    ),
    "myrm-agent-harness/src/myrm_agent_harness/toolkits/retriever/splitter/chunk_processor.py": (
        1,
        "runs on a chunk of fewer than _NAVIGATION_MAX_CHARS characters only",
    ),
}

# file -> (number of patterns, what is not bounded). Each one is a known gap waiting for a decision: read the links
# with utils.markdown_links, or move it up to the table above once its bound is shown.
_QUADRATIC_PENDING_DECISION: dict[str, tuple[int, str]] = {
    "myrm-agent-harness/src/myrm_agent_harness/backends/skills/scanning/patterns/exfiltration.py": (
        1,
        "`.*` before `](` restarts at every `![` of a line: 8,000 characters of them take 19 ms, 50,000 take 0.6 s",
    ),
}


def _pattern_text(node: ast.expr) -> str:
    """The literal characters of a pattern argument, an f-string's constant parts included."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str))
    return ""


def link_pattern_lines(source: str) -> list[int]:
    """Line numbers of ``re.<function>(pattern, ...)`` calls whose pattern literal joins a label and a target."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _RE_FUNCTIONS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "re"
        and node.args
        and _LINK_JOIN in _pattern_text(node.args[0])
    ]


@cache
def _patterns_per_file() -> dict[str, int]:
    found: dict[str, int] = {}
    for tree in _TREES:
        for path in tree.rglob("*.py"):
            lines = link_pattern_lines(path.read_text(encoding="utf-8"))
            if lines:
                found[path.relative_to(_ROOT).as_posix()] = len(lines)
    return found


def test_the_detector_finds_a_link_pattern_however_it_is_written() -> None:
    assert link_pattern_lines(r"LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')") == [1]
    assert link_pattern_lines(r"re.sub(r'\[(.+?)\]\((.+?)\)', repl, text)") == [1]
    assert link_pattern_lines('re.compile(r"!\\[x\\]\\(" r"https?://.*")') == [1]
    assert link_pattern_lines(r"re.compile(rf'\[[^\]]{{1,{LIMIT}}}\]\({target}\)')") == [1]
    assert link_pattern_lines(r"a = re.search(r'\]\(', x)" + "\n" + r"b = re.search(r'\]\(', y)") == [1, 2]


def test_the_detector_ignores_everything_that_is_not_a_link_pattern() -> None:
    assert link_pattern_lines(r"re.compile(r'\[\[([^\]|#]+)\]\]')") == []
    assert link_pattern_lines(r"re.compile(r'\]\s*\(')") == []
    assert link_pattern_lines(r"re.compile(pattern)") == []
    assert link_pattern_lines(r"re.compile(r'\[a\]' + SEPARATOR)") == []
    assert link_pattern_lines(r"re.escape(r'\]\(')") == []
    assert link_pattern_lines(r"other.compile(r'\]\(')") == []
    assert link_pattern_lines(r"note = r'\]\('") == []


def test_every_link_pattern_is_accounted_for() -> None:
    accounted = {**_SAFE_BY_CONSTRUCTION, **_QUADRATIC_PENDING_DECISION}
    unaccounted = {rel: count for rel, count in _patterns_per_file().items() if rel not in accounted}

    assert not unaccounted, (
        f"A pattern that reads [label](target) can start over from every unclosed '[': {unaccounted}. "
        "Read the links with myrm_agent_harness.utils.markdown_links (markdown_links, replace_markdown_links, "
        "or mask_unclosable_openers for a pattern that must keep its own shape), or list the file in "
        "_SAFE_BY_CONSTRUCTION with its number of patterns and the reason none of them restarts."
    )


def test_the_accounted_files_hold_exactly_the_patterns_they_are_listed_for() -> None:
    found = _patterns_per_file()
    accounted = {**_SAFE_BY_CONSTRUCTION, **_QUADRATIC_PENDING_DECISION}
    drifted = {rel: (count, found.get(rel, 0)) for rel, (count, _) in accounted.items() if found.get(rel, 0) != count}

    assert not drifted, (
        f"(listed, found) patterns differ for {drifted}. A file that no longer holds such a pattern leaves the "
        "lists; a file that gained one needs its bound shown, or the scan of utils.markdown_links instead."
    )


def test_a_file_is_listed_once_and_every_entry_gives_its_reason() -> None:
    assert not _SAFE_BY_CONSTRUCTION.keys() & _QUADRATIC_PENDING_DECISION.keys()
    entries = [*_SAFE_BY_CONSTRUCTION.values(), *_QUADRATIC_PENDING_DECISION.values()]
    assert all(count > 0 and reason.strip() for count, reason in entries)
