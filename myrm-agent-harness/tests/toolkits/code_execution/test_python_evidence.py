"""Tests for python_evidence — the syntax-tree test that tells Python source from shell text."""

import time

import pytest

from myrm_agent_harness.toolkits.code_execution.python_evidence import python_evidence


class TestParsedText:
    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ("import os", "python statement: Import"),
            ("from os import path", "python statement: ImportFrom"),
            ("def f():\n    pass", "python statement: FunctionDef"),
            ("async def f():\n    pass", "python statement: AsyncFunctionDef"),
            ("class A:\n    pass", "python statement: ClassDef"),
            ("for i in range(3):\n    print(i)", "python statement: For"),
            ("while True:\n    break", "python statement: While"),
            ("with open('a') as f:\n    pass", "python statement: With"),
            ("try:\n    pass\nexcept OSError:\n    pass", "python statement: Try"),
            ("if x:\n    pass", "python statement: If"),
            ("x = 1\nx += 2", "python statement: AugAssign"),
            ("x: int = 1", "python statement: AnnAssign"),
            ("del x", "python statement: Delete"),
            ("raise ValueError('x')", "python statement: Raise"),
            ("assert x", "python statement: Assert"),
            ("await f()", "python statement: Await"),
            ("print('hello')", "python call statement"),
            ("result = compute(1)", "python assignment from a call"),
        ],
    )
    def test_a_construct_a_shell_line_cannot_hold_is_python(self, text: str, reason: str) -> None:
        assert python_evidence(text) == reason

    @pytest.mark.parametrize(
        "text",
        ["", "   \n  ", "ls", "ls -la", "FOO=bar", "OUT=result.txt", "FLAGS=-la", "a/b", "{'k': 1}", "echo hi && ls"],
    )
    def test_text_that_merely_parses_or_is_a_shell_line_is_not_python(self, text: str) -> None:
        assert python_evidence(text) is None


class TestUnparsableText:
    def test_text_that_opens_like_a_program_is_python(self) -> None:
        assert python_evidence("import os\nprint(os.listdir('.')") == "python opening statement"
        assert python_evidence("from os import (") == "python opening statement"
        assert python_evidence("def f(:\n    pass") == "python opening statement"

    def test_leading_comments_and_blank_lines_are_skipped(self) -> None:
        assert python_evidence("# parse the log\n\nimport os\nprint(") == "python opening statement"

    def test_text_that_opens_any_other_way_is_left_to_the_shell(self) -> None:
        assert python_evidence("x = 41\nprint('set', x_state") is None
        assert python_evidence("importing things (") is None


class TestHeredocOpeners:
    """A heredoc that writes a file parses as a comparison and a shift; its body may parse too."""

    @pytest.mark.parametrize(
        "text",
        [
            "cat > notes.txt <<EOF\nimport os\nprint(os.getcwd())\nEOF",
            "cat > notes.txt <<'EOF'\nimport os\nEOF",
            'cat > notes.txt <<"EOF"\nimport os\nEOF',
            "cat <<'EOF' > notes.txt\nimport os\nEOF",
            "cat <<EOF >notes.txt\nfrom skills.x_skill import y\nEOF",
            "cat > notes.txt <<-EOF\nimport os\n\tEOF",
            "cd /ws && cat > notes.txt <<EOF\nimport os\nEOF",
        ],
        ids=["bare-marker", "single-quoted", "double-quoted", "marker-first", "marker-first-bare", "dash", "after-cd"],
    )
    def test_a_file_written_by_a_heredoc_is_shell_text(self, text: str) -> None:
        assert python_evidence(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "x = 1 << 3\nimport os",
            "flags = 1 << MASK\nimport os",
            "flags = 1 << MASK\nprint(flags)",
            "bits = 1 << n\nrows = [bits]\nimport os",
        ],
    )
    def test_a_real_left_shift_does_not_make_the_text_shell(self, text: str) -> None:
        assert python_evidence(text) is not None

    def test_a_string_that_holds_a_heredoc_is_no_shift(self) -> None:
        assert python_evidence('print("cat <<EOF")\nEOF') == "python call statement"

    def test_a_shift_followed_by_a_line_made_of_its_own_right_operand_is_an_opener(self) -> None:
        assert python_evidence("flags = 1 << MASK\nMASK\nimport os") is None

    def test_thousands_of_shifts_are_checked_in_one_pass(self) -> None:
        # Just under the size the parser accepts. One pass takes a few hundredths of a second; checking every shift
        # against every line takes most of a second.
        text = "\n".join("a<<b" for _ in range(6000)) + "\nimport os"
        assert len(text) < 32_768
        started = time.perf_counter()
        reason = python_evidence(text)
        assert time.perf_counter() - started < 0.4
        assert reason == "python statement: Import"
