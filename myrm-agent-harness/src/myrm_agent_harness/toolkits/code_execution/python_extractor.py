"""Python text helpers for bash commands: syntax pre-check, PTC import detection and the pipe-stdin surface.

[INPUT]
- utils.python_parse::PYTHON_PARSE_ERRORS (POS: What the Python parser raises for text that is not a usable program)

[OUTPUT]
- extract_python_from_pipe_stdin: Extract Python fed via ``quoted_payload | python3`` stdin.
- extract_cat_py_paths_from_pipe_feeders: Resolve ``.py`` paths from ``cat`` pipe feeder segments.
- validate_python_syntax: Pre-check extracted Python, compiled as the execution wrapper compiles it (top-level
  await allowed); an error names the line and column and quotes that line (cut to a window of 160 characters
  around the column).
- SKILL_IMPORT_RE: Compiled pattern for ``from skills.xxx_skill import`` detection.
- TOOLS_IMPORT_RE: Compiled pattern for ``from tools.xxx import`` detection.
- detect_ptc_imports: AST detection of every static and literal dynamic form of a skill or built-in tool import.
- SKILLS_NAMESPACE_ADVICE: Guidance for a failed ``No module named 'skills'`` import.

[POS]
Python text helpers of the skill executor, the bash tool's guards and the execution syntax pre-check. Which program a
command consists of is decided by ``python_program``, which builds on these helpers.
"""

from __future__ import annotations

import ast
import re

from myrm_agent_harness.utils.python_parse import PYTHON_PARSE_ERRORS


def extract_python_from_pipe_stdin(command: str) -> str | None:
    """Extract Python source piped to bare ``python3`` / ``python`` on stdin.

    Matches patterns like ``printf 'import x' | python3`` where the left segment
    holds a quoted script payload and the right segment is stdin-only python
    (no ``-c``, no ``.py`` path). Returns ``None`` when no such surface exists.
    """
    segments = _split_pipe_segments(command)
    if len(segments) < 2:
        return None
    if not _is_bare_stdin_python_receiver(segments[-1]):
        return None
    for feeder in reversed(segments[:-1]):
        payload = _extract_feeder_quoted_payload(feeder)
        if payload is not None:
            return payload
        payload = _extract_feeder_unquoted_payload(feeder)
        if payload is not None:
            return payload
    return None


def extract_cat_py_paths_from_pipe_feeders(command: str) -> list[str]:
    """Return ``.py`` file refs from ``cat path.py | python3``-style pipe feeders.

    Only applies when the final segment is bare stdin ``python3`` / ``python``
    (no ``-c``, ``-m``, or ``.py`` path on the receiver).
    """
    segments = _split_pipe_segments(command)
    if len(segments) < 2 or not _is_bare_stdin_python_receiver(segments[-1]):
        return []
    paths: list[str] = []
    for feeder in segments[:-1]:
        match = _CAT_PY_PATH_RE.search(feeder)
        if match is not None:
            paths.append(match.group(1))
    return list(dict.fromkeys(paths))


_SYNTAX_EXCERPT_CHARS = 160


def _describe_syntax_error(exc: SyntaxError, code: str) -> str:
    """What is wrong, where, and the text of that line, in one line the model can act on."""
    where = [f"SyntaxError: {exc.msg}"]
    if exc.lineno:
        where.append(f"line {exc.lineno}")
        if exc.offset:
            where.append(f"column {exc.offset}")
    message = ", ".join(where)
    # The tokenizer counts only \n, \r\n and \r as line ends; str.splitlines also splits on form feeds and the like.
    lines = code.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if exc.lineno and exc.lineno <= len(lines) and lines[exc.lineno - 1].strip():
        message += f": `{_excerpt(lines[exc.lineno - 1], exc.offset)}`"
    return message


def _excerpt(line: str, column: int | None) -> str:
    """The line as written (indentation is part of the diagnosis), windowed around the column when it is long."""
    line = line.rstrip()
    if len(line) <= _SYNTAX_EXCERPT_CHARS:
        return line
    start = max(0, (column or 1) - 1 - _SYNTAX_EXCERPT_CHARS // 2)
    end = start + _SYNTAX_EXCERPT_CHARS
    return f"{'...' if start else ''}{line[start:end]}{'...' if end < len(line) else ''}"


def validate_python_syntax(code: str) -> str | None:
    """Return ``None`` if *code* is valid Python, otherwise a human-readable error.

    Compiled the way the execution wrapper compiles user code, with top-level ``await``,
    ``async for``, ``async with`` and async comprehensions allowed: a script the sandbox can
    run passes, and a construct it cannot run (a top-level ``return``) is refused wherever it
    appears in the script.
    """
    try:
        compile(code, "<string>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT, dont_inherit=True)
    except SyntaxError as exc:
        return _describe_syntax_error(exc, code)
    except PYTHON_PARSE_ERRORS as exc:
        return (
            f"Python code cannot be parsed ({type(exc).__name__}): "
            "it is nested or chained too deeply, or holds text that cannot be encoded"
        )
    return None


SKILL_IMPORT_RE = re.compile(r"from\s+(?:skills\.)?([\w]+_skill)(?:\.\w+)?\s+import")
TOOLS_IMPORT_RE = re.compile(r"(?:from\s+tools\.\w+\s+import|import\s+tools\.)")

_SKILL_MODULE_MARKER = "_skill"
_TOOLS_MODULE = "tools"
_SKILLS_PACKAGE = "skills"

SKILLS_NAMESPACE_ADVICE = (
    "Do not install anything: `skills` is the virtual namespace of MCP skills, not a package. "
    "Import a skill with `from skills.<skill_name> import <function>` (names from the skill SOP), either in the "
    "inline Python of `bash_code_execute_tool` or in a saved script started as exactly `python file.py` "
    "(no flags, arguments, pipes or env prefix; read-only tools only). "
    "A bare `import skills` or any other way of starting a script does not get that namespace."
)
"""What to tell a model whose code failed with ``No module named 'skills'``.

The namespace is armed only for inline code that ``python_program.extract_python_from_bash`` reads as a skill call (see
``SKILL_IMPORT_RE``) and for a saved script the host reads itself (see ``saved_script``).
"""


def detect_ptc_imports(code: str) -> tuple[bool, str | None] | None:
    """Detect PTC imports in Python source using AST.

    Regex matching only covers ``from x import y``; it silently misses
    ``import skills.foo_skill``, ``import skills.foo_skill as s``, and dynamic
    ``importlib.import_module`` / ``__import__`` forms, which made PTC routing
    fail-open for perfectly valid scripts. AST covers every static form.

    Returns:
        ``(True, skill_name)`` for an MCP skill import, ``(True, "__builtin__")``
        for ``tools.*``, ``(False, None)`` when the code parses but imports
        nothing PTC-related, or ``None`` when the source is not parseable so the
        caller can fall back to regex heuristics.
    """
    try:
        tree = ast.parse(code)
    except PYTHON_PARSE_ERRORS:
        return None

    skill_name: str | None = None
    builtin_found = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == _TOOLS_MODULE:
                    builtin_found = True
                elif parts[0] == _SKILLS_PACKAGE and len(parts) > 1 and parts[1].endswith(_SKILL_MODULE_MARKER):
                    skill_name = skill_name or parts[1]
                elif parts[0].endswith(_SKILL_MODULE_MARKER):
                    skill_name = skill_name or parts[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            parts = node.module.split(".")
            if parts[0] == _TOOLS_MODULE:
                builtin_found = True
            elif parts[0] == _SKILLS_PACKAGE and len(parts) > 1 and parts[1].endswith(_SKILL_MODULE_MARKER):
                skill_name = skill_name or parts[1]
            elif parts[0].endswith(_SKILL_MODULE_MARKER):
                skill_name = skill_name or parts[0]

    if skill_name:
        return True, skill_name
    if builtin_found:
        return True, "__builtin__"

    dynamic = _detect_dynamic_ptc_import(tree)
    if dynamic is not None:
        return dynamic
    return False, None


def _detect_dynamic_ptc_import(tree: ast.AST) -> tuple[bool, str | None] | None:
    """Detect ``importlib.import_module("skills.x_skill")`` / ``__import__(...)``.

    Only literal first arguments are inspected: a computed module name cannot be
    resolved statically, and guessing would route non-PTC code into the sandbox.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_dynamic = (isinstance(func, ast.Name) and func.id == "__import__") or (
            isinstance(func, ast.Attribute) and func.attr == "import_module"
        )
        if not is_dynamic or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        parts = first.value.split(".")
        if parts[0] == _TOOLS_MODULE:
            return True, "__builtin__"
        if parts[0] == _SKILLS_PACKAGE and len(parts) > 1 and parts[1].endswith(_SKILL_MODULE_MARKER):
            return True, parts[1]
        if parts[0].endswith(_SKILL_MODULE_MARKER):
            return True, parts[0]
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_BARE_PYTHON_BIN_RE = re.compile(r"^python3?\b", re.IGNORECASE)

_CAT_PY_PATH_RE = re.compile(r"\bcat\s+([^\s|]+\.py)\b", re.IGNORECASE)


def _scan_quoted(text: str, quote: str) -> str | None:
    """Walk *text* respecting backslash escapes and return content up to the
    unescaped closing *quote*.  Returns ``None`` if no valid close is found."""
    buf: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            buf.append(text[i + 1])
            i += 2
            continue
        if ch == quote:
            return "".join(buf)
        buf.append(ch)
        i += 1
    return "".join(buf) if buf else None


def _split_pipe_segments(command: str) -> list[str]:
    """Split on unquoted ``|`` pipeline operators."""
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    index = 0
    length = len(command)

    while index < length:
        char = command[index]
        if char == "'" and not in_double:
            in_single = not in_single
            current.append(char)
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            current.append(char)
            index += 1
            continue
        if char == "\\" and in_double and index + 1 < length:
            current.append(char)
            current.append(command[index + 1])
            index += 2
            continue
        if char == "|" and not in_single and not in_double:
            segments.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1

    segments.append("".join(current))
    return segments


def _is_bare_stdin_python_receiver(segment: str) -> bool:
    """True when *segment* is ``python3`` / ``python`` reading stdin (no ``-c``/``.py``)."""
    stripped = segment.strip()
    if not stripped or not _BARE_PYTHON_BIN_RE.match(stripped):
        return False
    if re.search(r"\s-c\b", stripped, re.IGNORECASE):
        return False
    if re.search(r"\.py\b", stripped, re.IGNORECASE):
        return False
    return not re.search(r"\s-m\b", stripped, re.IGNORECASE)


def _extract_feeder_quoted_payload(segment: str) -> str | None:
    """Return the first quote-delimited payload in a pipe feeder segment."""
    for quote in ('"', "'"):
        start = segment.find(quote)
        if start < 0:
            continue
        payload = _scan_quoted(segment[start + 1 :], quote)
        if payload is not None:
            return payload
    return None


def _extract_feeder_unquoted_payload(segment: str) -> str | None:
    """Return unquoted ``echo``/``printf`` body used as stdin script payload."""
    stripped = segment.strip()
    for command in ("echo", "printf"):
        prefix = f"{command} "
        if not stripped.startswith(prefix):
            continue
        rest = stripped[len(prefix) :].strip()
        while rest.startswith("-") and " " in rest:
            _, rest = rest.split(" ", 1)
            rest = rest.strip()
        if not rest or rest[0] in ('"', "'"):
            return None
        return rest
    return None
