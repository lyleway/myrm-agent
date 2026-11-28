"""Evidence that a text is Python source and not a shell command line.

A shell command often parses as Python (``ls``, ``ls -la``, ``FOO=bar``), so parsing alone proves nothing: the text
needs a construct a shell line cannot be. A text that does not parse is still Python when it opens like a program, so
the interpreter's SyntaxError names the model's own line where a shell would only answer "command not found".

[INPUT]
- utils.python_parse::PYTHON_PARSE_ERRORS (POS: What the Python parser raises for text that is not a usable program)

[OUTPUT]
- python_evidence: Why a text is Python source, or ``None`` when a shell should run it.

[POS]
Syntax-tree test that tells Python source from shell text. A pure function of the text, because the executor, the
permission preflight, the tool's guards and the readers of a command's Python program each ask about the same command
and must get the same answer.
"""

from __future__ import annotations

import ast
import re
import warnings

from myrm_agent_harness.utils.python_parse import PYTHON_PARSE_ERRORS

# Parsing is synchronous and runs on the event loop, so past this size the text is judged by its opening line alone.
_MAX_PARSE_CHARS = 32_768

# Parsing a shell command can make the compiler warn (``grep "\d+"`` holds an invalid Python escape). The text is parsed
# under its own file name, so one process-wide filter silences exactly that and leaves every other warning alone.
_PARSE_FILENAME = "<command-text>"
warnings.filterwarnings("ignore", category=SyntaxWarning, module=re.escape(_PARSE_FILENAME))

# Constructs a shell command line cannot contain: text that parses and holds one of them is Python.
_PYTHON_ONLY_NODES: tuple[type[ast.AST], ...] = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.TryStar,
    ast.If,
    ast.AugAssign,
    ast.AnnAssign,
    ast.Delete,
    ast.Raise,
    ast.Assert,
    ast.Await,
    ast.Match,
)

# What text that does not parse may still open with to be Python.
_PYTHON_OPENING_RE = re.compile(
    r"^(?:import\s+[A-Za-z_]|from\s+[A-Za-z_.]+\s+import\b|def\s+[A-Za-z_]|async\s+def\s|class\s+[A-Za-z_])"
)


def python_evidence(text: str) -> str | None:
    """Why ``text`` is Python source, or ``None`` when a shell should run it."""
    stripped = text.strip()
    if not stripped:
        return None
    tree = _parse(stripped)
    if tree is None:
        return "python opening statement" if _PYTHON_OPENING_RE.match(_first_code_line(stripped)) else None
    if _writes_heredoc(tree, stripped):
        return None
    for node in ast.walk(tree):
        if isinstance(node, _PYTHON_ONLY_NODES):
            return f"python statement: {type(node).__name__}"
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return "python call statement"
        # Not any assignment: ``OUT=result.txt`` and ``FLAGS=-la`` parse as ones too, while a shell word cannot hold a call.
        if isinstance(node, ast.Assign) and any(isinstance(inner, ast.Call) for inner in ast.walk(node.value)):
            return "python assignment from a call"
    return None


def _first_code_line(text: str) -> str:
    """The first line that is neither blank nor a comment, stripped."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _parse(text: str) -> ast.Module | None:
    """The syntax tree of ``text``, or ``None`` when it is too large or does not parse."""
    if len(text) > _MAX_PARSE_CHARS:
        return None
    try:
        return ast.parse(text, filename=_PARSE_FILENAME)
    except PYTHON_PARSE_ERRORS:
        return None


def _writes_heredoc(tree: ast.Module, text: str) -> bool:
    """Whether a statement of ``text`` is a shell heredoc opener whose closing marker follows on a line of its own.

    ``cat > notes.txt <<EOF`` and ``cat <<'EOF' > notes.txt`` parse as a comparison and a left shift, and the body
    after them may parse as statements too, so the whole text can look like a program while a shell would read it as
    one command that writes a file. A left shift whose right operand is a name or a string (``<<EOF``, ``<<'EOF'``,
    ``<<-EOF``) and whose text stands alone on a later line is that opener: a real shift is never followed by a
    line made of its own right operand.
    """
    last_line_of: dict[str, int] | None = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.LShift) and node.end_lineno):
            continue
        marker = _heredoc_marker(node.right)
        if marker is None:
            continue
        if last_line_of is None:
            lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            # One pass for all shifts: the text may hold thousands of them, and each asks the same question.
            last_line_of = {line.strip(): index for index, line in enumerate(lines)}
        if last_line_of.get(marker, -1) >= node.end_lineno:
            return True
    return False


def _heredoc_marker(operand: ast.expr) -> str | None:
    """The marker a shift's right operand would be as a heredoc: ``EOF``, ``'EOF'`` or ``-EOF`` (the ``<<-`` form)."""
    if isinstance(operand, ast.UnaryOp) and isinstance(operand.op, ast.USub):
        operand = operand.operand
    if isinstance(operand, ast.Name):
        return operand.id
    if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
        return operand.value
    return None
