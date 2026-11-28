"""The Python program a bash command consists of.

The bash tool runs a command in its Python wrapper instead of a shell only when the command is a Python program and
nothing else. The wrapper drops every other word of the command, and Python must receive the text a shell would have
handed it (quote removal, ``$`` and backtick expansion, heredoc expansion), so a command that is more than the program,
or that a shell would read differently, stays with the shell.

[INPUT]
- core.security.shell_command_lexer::has_word_expansion, shell_words, split_command_spans (POS: Shared lexical layer of the shell command security checks; every reader of a command line agrees on its words)
- toolkits.code_execution.python_evidence::python_evidence (POS: Syntax-tree test that tells Python source from shell text)
- toolkits.code_execution.python_extractor::SKILL_IMPORT_RE, TOOLS_IMPORT_RE, validate_python_syntax (POS: Python text helpers of the skill executor, the bash tool's guards and the execution syntax pre-check)

[OUTPUT]
- PythonWrapper: How a command wraps its Python program (``python -c``, a ``python3`` heredoc, a ``cat`` heredoc that writes a ``.py`` file).
- PythonProgram: The Python a command consists of, the wrapper it came in and why it was recognised.
- read_python_program: The Python program a command consists of, or ``None`` when a shell has to run it.
- extract_python_from_bash: The Python source a command carries, for PTC detection and approval.

[POS]
Reader that decides which commands the bash tool hands to Python. Shared by the language router, the skill executor and
the PTC verifier, so all three see the same program in the same command.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from enum import StrEnum

from myrm_agent_harness.core.security.shell_command_lexer import has_word_expansion, shell_words, split_command_spans
from myrm_agent_harness.toolkits.code_execution.python_evidence import python_evidence
from myrm_agent_harness.toolkits.code_execution.python_extractor import (
    SKILL_IMPORT_RE,
    TOOLS_IMPORT_RE,
    validate_python_syntax,
)


class PythonWrapper(StrEnum):
    """How a command wraps the Python program it consists of."""

    INLINE_FLAG = "python -c"
    STDIN_HEREDOC = "python3 heredoc"
    FILE_HEREDOC = "cat heredoc writing a .py file"


_INLINE_REASON = "python -c command (extracted)"
_INLINE_RAW_REASON = "python -c command (raw fallback after quote extraction failed)"
_EMBEDDED_REASON = "embedded python extracted from bash wrapper"


@dataclass(frozen=True)
class PythonProgram:
    """The Python a command consists of, and how it was recognised."""

    code: str
    wrapper: PythonWrapper
    raw: bool = False
    """An unquoted ``python -c`` payload, taken as written so that Python's own parser reports what is wrong with it."""

    @property
    def reason(self) -> str:
        """Why the command is Python, for the router's log line."""
        if self.raw:
            return _INLINE_RAW_REASON
        return _INLINE_REASON if self.wrapper is PythonWrapper.INLINE_FLAG else _EMBEDDED_REASON


def read_python_program(command: str) -> PythonProgram | None:
    """The Python program *command* consists of, or ``None`` when a shell has to run it.

    A command is a program only when it is nothing but that program, alone or after ``cd ... &&``, and the text Python
    receives is what the shell would have passed on:

    1. ``python3 -c "..."`` / ``python3 -c '...'``: the one word after ``-c``, quotes resolved by the shell lexer and
       free of expansions, with at most a closing ``;`` or ``2>&1`` after it; an unquoted payload is kept as Python so
       the parser can report it
    2. ``python3 <<EOF ... EOF`` / ``python3 - <<EOF ... EOF``: a heredoc whose body the shell passes on unchanged
    3. ``cat > file.py <<EOF ... EOF`` / ``cat <<EOF > file.py ... EOF``: a heredoc that writes a Python script,
       alone or followed by ``python3 file.py``
    """
    program = _read_python_c(command)
    if program is not None:
        return program
    # Each ``<<`` that has no closing marker costs a scan of the rest of the command, so a command made of thousands of
    # openers would stall the router. No real program carries more than a few (the archive tops out at four).
    if command.count("<<") > _MAX_HEREDOC_OPENERS:
        return None
    return _read_heredoc(command) or _read_cat_heredoc(command)


def extract_python_from_bash(command: str) -> str | None:
    """The Python source *command* carries, or ``None`` when it carries none.

    That is the program ``read_python_program`` reads (an unquoted ``python -c`` payload is left to the parser), or, for
    raw source, the command itself when it is Python that imports a skill or a built-in tool.
    """
    program = read_python_program(command)
    if program is not None:
        return None if program.raw else program.code
    return command if _is_ptc_source(command) else None


_PYTHON_C_RE = re.compile(r"python3?\s+-c(?=\s)")
_STDERR_MERGE = "2>&1"

# The only text that may stand in front of a program that runs alone: ``cd`` statements (the Python wrapper already runs
# in the workspace the model works in), joined by ``&&``, ``||``, ``;`` or a line break. A target the shell would expand
# (``$``, a backtick) is not a plain directory, so it is not in the set. A statement is matched one at a time with only
# spaces and tabs around it, so every character of the prefix has exactly one reading and the scan stays linear.
_CD_TARGET = r"""(?:'[^']*'|"[^"$`\\]*"|[^\s;&|<>()$`\\'"]+)"""
_CD_STATEMENT_RE = re.compile(rf"cd[ \t]+{_CD_TARGET}(?:[ \t]+2>[ \t]*/dev/null)?[ \t]*(?:&&|\|\||;|\n)")

_HEREDOC_OPEN = r"<<(?P<dash>-?)\s*(?P<quote>['\"]?)(?P<marker>\w+)['\"]?"
_MAX_HEREDOC_OPENERS = 32

# ``python3 <<EOF`` and the stdin form ``python3 - <<EOF``.
_HEREDOC_RE = re.compile(
    rf"python3?\s+(?:-\s+)?{_HEREDOC_OPEN}\s*\n(?P<body>.+?)\n(?P=marker)\b",
    re.DOTALL,
)

# A ``cat`` that writes a ``.py`` file, at the start of a line or of a ``&&`` / ``;`` step, with the redirect on either
# side of the heredoc marker: ``cat > job.py <<EOF`` and ``cat <<EOF > job.py`` are the same command to the shell.
# Only a Python target is read: any other file (JSON, ini, ``.env``, notes) is written by the shell verbatim, even when
# its text happens to parse as Python.
_CAT_HEREDOC_RES = tuple(
    re.compile(rf"(?:^|&&|;)[ \t]*(?P<cat>cat)\s+{shape}\s*\n(?P<body>.+?)\n(?P=marker)\b", re.DOTALL | re.MULTILINE)
    for shape in (
        rf">\s*(?P<path>\S+\.py)['\"]?\s+{_HEREDOC_OPEN}",
        rf"{_HEREDOC_OPEN}\s*>\s*(?P<path>\S+\.py)['\"]?(?=\s|$)",
    )
)

# What a shell does to the body of a heredoc whose marker is not quoted: ``$`` and the backtick expand, and a backslash
# in front of ``$``, a backtick, a backslash or a line break is consumed.
_HEREDOC_EXPANDS = re.compile(r"[$`]|\\[\\\n]")

# The command that runs the file a ``cat`` heredoc has just written: ``python3 job.py``, nothing else.
_RUN_OF_FILE_RE = re.compile(r"\s*(?:(?:&&|;)\s*)?python3?\s+(?P<script>\S+)\s*")

# Imports that arm the skill runtime: ``from skills.x_skill import``, ``from tools.x import`` and the import-statement
# and dynamic-import forms ``SKILL_IMPORT_RE`` does not cover (``import skills.x_skill``, ``import_module(...)``).
_IMPORT_STYLE_SKILL_RE = re.compile(
    r"(?:^\s*import\s+(?:skills\.)?[\w]+_skill\b)"
    r"|(?:import_module\s*\(\s*['\"]skills\.[\w]+_skill['\"])"
    r"|(?:__import__\s*\(\s*['\"](?:skills\.)?[\w]+_skill['\"])",
    re.MULTILINE,
)


def _after_cd_only(command: str, start: int) -> bool:
    """Whether nothing but ``cd`` statements stands in front of ``command[start:]``."""
    position = 0
    while True:
        while position < start and command[position].isspace():
            position += 1
        if position >= start:
            return True
        statement = _CD_STATEMENT_RE.match(command, position, start)
        if statement is None:
            return False
        position = statement.end()


def _runs_alone(command: str, start: int, end: int, written: str | None = None) -> bool:
    """Whether the construct at ``command[start:end]`` is all a shell would run.

    Only ``cd`` statements may precede it. After it there is nothing, or, for a heredoc that wrote the file *written*,
    the command that runs that file.
    """
    if not _after_cd_only(command, start):
        return False
    tail = command[end:]
    if not tail.strip():
        return True
    run = _RUN_OF_FILE_RE.fullmatch(tail)
    if run is None or written is None:
        return False
    return posixpath.normpath(run["script"].strip("'\"")) == posixpath.normpath(written)


def _read_python_c(command: str) -> PythonProgram | None:
    """``python -c`` as the whole command: the payload is the word the shell passes after ``-c``."""
    match = _PYTHON_C_RE.search(command)
    if match is None or not _after_cd_only(command, match.start()):
        return None
    payload = command[match.end() :].lstrip()
    if payload[:1].isalpha() or payload[:1] == "_":
        # Not quoted: the shell would split it into words and run the rest as commands. Kept as Python so the parser
        # reports the real problem instead of a cascade of "not found".
        return PythonProgram(payload.rstrip(), PythonWrapper.INLINE_FLAG, raw=True)
    # A closing ``;`` ends the command and nothing else; ``2>&1`` only merges the stderr the tool reports anyway.
    statement = command[match.start() :].rstrip().rstrip(";")
    spans = split_command_spans(statement)
    if len(spans) != 1:
        return None
    text = statement[spans[0][0] : spans[0][1]]
    words = shell_words(text)
    if words is None or len(words) < 3 or words[3:] not in ([], [_STDERR_MERGE]) or has_word_expansion(text):
        return None
    return PythonProgram(words[2], PythonWrapper.INLINE_FLAG)


def _heredoc_body_is_literal(match: re.Match[str]) -> bool:
    """Whether the shell hands the heredoc body to its reader exactly as written."""
    body = match["body"]
    if match["dash"] and any(line.startswith("\t") for line in body.splitlines()):
        return False
    return bool(match["quote"]) or _HEREDOC_EXPANDS.search(body) is None


def _read_heredoc(command: str) -> PythonProgram | None:
    """``python3 <<EOF`` / ``python3 - <<EOF`` as the whole command, its body passed on unchanged."""
    match = _HEREDOC_RE.search(command)
    if match is None or not _runs_alone(command, match.start(), match.end()) or not _heredoc_body_is_literal(match):
        return None
    return PythonProgram(match["body"].strip(), PythonWrapper.STDIN_HEREDOC)


def _read_cat_heredoc(command: str) -> PythonProgram | None:
    """Python body of a ``cat > file.py << EOF ... EOF`` shell wrapper, alone or followed by ``python3 file.py``.

    Read only when the target is a ``.py`` file and the body parses as valid Python. Every other heredoc write (a JSON
    or ini file, ``.env``, notes, YAML, a shell script) is left to the shell so the file is written verbatim: running
    its text as Python would report success and leave no file behind.
    """
    for pattern in _CAT_HEREDOC_RES:
        match = pattern.search(command)
        if match is None:
            continue
        body = match["body"].strip()
        written = match["path"].strip("'\"")
        if (
            body
            and validate_python_syntax(body) is None
            and _runs_alone(command, match.start("cat"), match.end(), written)
            and _heredoc_body_is_literal(match)
        ):
            return PythonProgram(body, PythonWrapper.FILE_HEREDOC)
        return None
    return None


def _is_ptc_source(command: str) -> bool:
    """Whether *command* is Python source that imports a skill or a built-in tool, not shell text that mentions one."""
    imports_ptc = (
        SKILL_IMPORT_RE.search(command) or TOOLS_IMPORT_RE.search(command) or _IMPORT_STYLE_SKILL_RE.search(command)
    )
    return imports_ptc is not None and python_evidence(command) is not None
