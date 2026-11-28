"""Code type detector for bash vs Python execution routing.

Detects whether user input is Python or Bash and extracts the Python program a command consists of.
Framework-agnostic; consumed by BashExecutor and executors.

[INPUT]
- python_program::PythonWrapper, read_python_program (POS: Reader that decides which commands the bash tool hands to Python)
- python_evidence::python_evidence (POS: Syntax-tree test that tells Python source from shell text)
- utils.text_utils::unwrap_markdown_fence (POS: text utilities; unwraps one Markdown fence around a command)

[OUTPUT]
- CodeType: Python vs Bash enum
- CodeDetectionResult: detection outcome with extracted code and the wrapper it came in
- CodeTypeDetector: detector class
- code_detector: module singleton
- detect_for_execution: the route of a command as the bash tool takes it, with the Python to run
- runs_as_python: whether the bash tool hands a command to the Python wrapper instead of a shell

[POS]
Pure code analysis with zero agent/ runtime dependencies. Lives in toolkits alongside the readers it composes: a command
is Python when it is one Python program in a wrapper (``python_program``) or when its text is Python source
(``python_evidence``), never by line counts or keywords; everything else is a shell command.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from myrm_agent_harness.toolkits.code_execution.python_evidence import python_evidence
from myrm_agent_harness.toolkits.code_execution.python_program import PythonWrapper, read_python_program
from myrm_agent_harness.utils.text_utils import unwrap_markdown_fence


class CodeType(StrEnum):
    PYTHON = "python"
    BASH = "bash"


@dataclass
class CodeDetectionResult:
    code_type: CodeType
    extracted_code: str
    detection_reason: str = ""
    wrapper: PythonWrapper | None = None
    """How the command wraps the Python it runs; ``None`` for a shell command and for Python source given as it is."""


class CodeTypeDetector:
    """Detect Python vs Bash and extract the Python program of a command.

    Detection order:
    1. The command is exactly one Python program in a wrapper (``python -c``, a ``python3`` heredoc, a ``cat`` heredoc
       that writes a ``.py`` file) → Python, the program extracted
    2. The text is Python source: it parses and holds a construct a shell line cannot (an import, a ``def``, a loop, a
       call statement, an assignment from a call, ``await``), or it does not parse but opens like Python → Python
    3. Default → Bash
    """

    def detect(self, command: str) -> CodeDetectionResult:
        program = read_python_program(command)
        if program is not None:
            return CodeDetectionResult(
                code_type=CodeType.PYTHON,
                extracted_code=program.code,
                detection_reason=program.reason,
                wrapper=program.wrapper,
            )

        evidence = python_evidence(command)
        if evidence:
            return CodeDetectionResult(
                code_type=CodeType.PYTHON,
                extracted_code=command,
                detection_reason=evidence,
            )

        return CodeDetectionResult(
            code_type=CodeType.BASH,
            extracted_code=command,
            detection_reason="default to bash",
        )


code_detector = CodeTypeDetector()


def detect_for_execution(command: str) -> CodeDetectionResult:
    """The route the bash tool gives ``command``: the executor strips it and unwraps one Markdown fence first.

    A check that depends on the language of a command asks here, so a command is judged in the language it runs
    in: Python wrapped in a fence is still Python.
    """
    return code_detector.detect(unwrap_markdown_fence(command.strip()))


def runs_as_python(command: str, *, run_in_background: bool = False) -> bool:
    """Whether the bash tool hands ``command`` to the Python wrapper instead of a shell.

    A background spawn is always read by a shell, whatever the text looks like. Otherwise the route is
    ``detect_for_execution``'s, so a check that only makes sense for shell syntax can stand down exactly when
    no shell reads the text.
    """
    return not run_in_background and detect_for_execution(command).code_type == CodeType.PYTHON
