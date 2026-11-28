"""Tests for python_program — which Python program a bash command consists of."""

import time

import pytest

from myrm_agent_harness.toolkits.code_execution.python_extractor import validate_python_syntax
from myrm_agent_harness.toolkits.code_execution.python_program import (
    PythonProgram,
    PythonWrapper,
    extract_python_from_bash,
    read_python_program,
)


class TestExtractPythonFromBash:
    def test_python_c_double_quotes(self):
        assert extract_python_from_bash('python3 -c "print(1)"') == "print(1)"

    def test_python_c_single_quotes_with_escape(self):
        cmd = r"python -c 'print(\"hi\")'"
        result = extract_python_from_bash(cmd)
        assert result is not None
        assert "print" in result

    def test_python_c_empty_rest_returns_none(self):
        assert extract_python_from_bash("python3 -c ") is None

    def test_heredoc_extraction(self):
        cmd = "python3 <<EOF\nimport os\nprint(os.getcwd())\nEOF"
        result = extract_python_from_bash(cmd)
        assert result is not None
        assert "import os" in result

    def test_cat_heredoc_extraction_with_trailing_python3(self):
        cmd = (
            "cat > /tmp/query_12306.py << 'EOF'\n"
            "import asyncio\n"
            "from skills.mcp_12306_skill import get_station_code_of_citys\n"
            "asyncio.run(get_station_code_of_citys(citys='北京'))\n"
            "EOF\n"
            "python3 /tmp/query_12306.py"
        )
        result = extract_python_from_bash(cmd)
        assert result is not None
        assert "import asyncio" in result
        assert "get_station_code_of_citys" in result
        assert "cat >" not in result
        assert validate_python_syntax(result) is None

    def test_cat_heredoc_shell_wrapper_does_not_return_raw_command(self):
        cmd = "cat > /tmp/x.py << EOF\nfrom skills.mcp_12306_skill import get_tickets\nEOF"
        result = extract_python_from_bash(cmd)
        assert result == "from skills.mcp_12306_skill import get_tickets"

    def test_skill_import_inside_shell_wrapper_returns_none(self):
        cmd = "cat broken\nfrom skills.daily_briefing_skill import run"
        assert extract_python_from_bash(cmd) is None

    def test_cat_heredoc_yaml_content_returns_none(self):
        cmd = (
            "cat > .myrm/filters.yaml << 'EOF'\n"
            "filters:\n"
            "  - name: e2e-filter-run\n"
            "    match_command: 'run\\\\.sh'\n"
            "EOF"
        )
        assert extract_python_from_bash(cmd) is None

    def test_cat_heredoc_shell_script_returns_none(self):
        cmd = "cat > run.sh << 'EOF'\n#!/bin/bash\necho 'E2E_BEGIN_LINE ok'\nEOF"
        assert extract_python_from_bash(cmd) is None

    def test_cat_heredoc_python_file_still_extracted(self):
        cmd = "cat > /tmp/run.py << 'EOF'\nimport os\nprint(os.getcwd())\nEOF"
        result = extract_python_from_bash(cmd)
        assert result is not None
        assert "import os" in result

    @pytest.mark.parametrize("interpreter", ["python3", "python"])
    def test_stdin_heredoc_extraction(self, interpreter):
        cmd = f"{interpreter} - <<'PY'\nimport asyncio\nfrom skills.mcp_k8s_ops_skill import list_pods\nPY"
        assert extract_python_from_bash(cmd) == "import asyncio\nfrom skills.mcp_k8s_ops_skill import list_pods"

    @pytest.mark.parametrize(
        "prefix",
        ["cd /workspace && ", "cd /workspace 2>/dev/null || cd .; "],
    )
    def test_stdin_heredoc_after_cd_prefix(self, prefix):
        cmd = f"{prefix}python3 - <<'PY'\nprint('ok')\nPY"
        assert extract_python_from_bash(cmd) == "print('ok')"

    def test_cat_heredoc_with_redirect_after_marker(self):
        cmd = "cat << 'EOF' > /tmp/step1.py\nimport os\nprint(os.getcwd())\nEOF\npython3 /tmp/step1.py"
        assert extract_python_from_bash(cmd) == "import os\nprint(os.getcwd())"

    def test_cat_heredoc_after_cd_prefix(self):
        cmd = "cd /workspace && cat > /tmp/step1.py <<'PY'\nimport os\nprint(os.getcwd())\nPY\npython3 /tmp/step1.py"
        assert extract_python_from_bash(cmd) == "import os\nprint(os.getcwd())"

    def test_cat_heredoc_without_space_after_redirect(self):
        assert extract_python_from_bash("cat >/tmp/x.py <<EOF\nprint(1)\nEOF") == "print(1)"

    def test_cat_heredoc_redirect_after_marker_keeps_non_python_verbatim(self):
        cmd = "cat <<'EOF' > conf.yaml\nfilters:\n  - name: a\nEOF"
        assert extract_python_from_bash(cmd) is None

    @pytest.mark.parametrize(
        "command",
        [
            "cat > data.json <<'EOF'\n{\"a\": 1}\nEOF",
            "cat > .env <<'EOF'\nPORT=8080\nWORKERS=4\nEOF",
            "cat > settings.ini <<EOF\nDEBUG=True\nEOF",
            "cat > notes.txt <<'EOF'\nhello\nworld\nEOF",
            "cat > requirements.txt <<'EOF'\nnumpy\npandas\nEOF",
            "cat <<'EOF' > cfg.json\n{\"k\": [1, 2]}\nEOF",
            "cat > job.py.bak <<'EOF'\nprint(1)\nEOF",
            "cd /workspace && cat > data.json <<'EOF'\n{\"a\": 1}\nEOF",
        ],
        ids=["json", "env", "ini", "txt", "requirements", "json-marker-first", "py-backup", "after-cd"],
    )
    def test_cat_heredoc_to_a_non_python_file_is_not_rewritten(self, command):
        """Text that merely parses as Python (a dict, ``KEY=1``, bare words) must still be written as a file."""
        assert extract_python_from_bash(command) is None

    @pytest.mark.parametrize(
        "command",
        [
            "cat > job.py <<'EOF'\nprint(1)\nEOF",
            "cat >job.py <<EOF\nprint(1)\nEOF",
            "cat > 'job.py' <<'EOF'\nprint(1)\nEOF",
            'cat > "job.py" <<EOF\nprint(1)\nEOF',
            "cat <<'EOF' > job.py\nprint(1)\nEOF",
            "cat <<'EOF' > 'job.py'\nprint(1)\nEOF\npython3 job.py",
            "cd /workspace && cat > sub/job.py <<'EOF'\nprint(1)\nEOF",
        ],
        ids=["plain", "no-space", "single-quoted", "double-quoted", "marker-first", "marker-first-quoted", "after-cd"],
    )
    def test_cat_heredoc_to_a_python_file_is_extracted(self, command):
        assert extract_python_from_bash(command) == "print(1)"

    def test_cat_heredoc_without_redirect_is_not_a_file_write(self):
        cmd = "cat <<EOF\nfrom skills.mcp_k8s_ops_skill import list_pods\nEOF"
        assert extract_python_from_bash(cmd) is None

    def test_skill_import_raw_python(self):
        cmd = "from skills.daily_briefing_skill import run"
        assert extract_python_from_bash(cmd) == cmd

    def test_tools_import_raw_python(self):
        cmd = "from tools.notify import send"
        assert extract_python_from_bash(cmd) == cmd

    def test_non_python_command_returns_none(self):
        assert extract_python_from_bash("ls -la") is None

    def test_unclosed_quote_is_left_to_the_shell(self):
        assert extract_python_from_bash('python3 -c "unclosed') is None


SKILL_CALL = "from skills.x_skill import y; y()"


class TestAProgramIsTheWholeCommand:
    """A command is handed to Python only when it is that program and nothing else a shell would run."""

    @pytest.mark.parametrize(
        ("command", "code"),
        [
            ('python3 -c "print(1)"', "print(1)"),
            ("python3 -c 'print(1)'", "print(1)"),
            ("python -c 'print(1)'", "print(1)"),
            ('python3 -c "print(1)"\n', "print(1)"),
            ('python3 -c "\nimport sys\nprint(sys.version_info[0])\n"', "\nimport sys\nprint(sys.version_info[0])\n"),
            ('python3 -c "print(1)";', "print(1)"),
            ('python3 -c "print(1)" 2>&1', "print(1)"),
            ('cd /ws && python3 -c "print(1)"', "print(1)"),
            ("cd '/my ws' && python3 -c 'print(1)'", "print(1)"),
            ('cd /ws && cd . && python3 -c "print(1)"', "print(1)"),
            ('cd /ws 2>/dev/null || cd .; python3 -c "print(1)"', "print(1)"),
            ("cd /ws\npython3 -c 'print(1)'", "print(1)"),
        ],
    )
    def test_python_c_alone_is_a_program(self, command, code):
        program = read_python_program(command)
        assert program is not None
        assert program.code == code
        assert program.wrapper is PythonWrapper.INLINE_FLAG
        assert not program.raw
        assert extract_python_from_bash(command) == code

    @pytest.mark.parametrize(
        "command",
        [
            'echo PREFIX && python3 -c "print(1)"',
            'touch marker.txt && python3 -c "print(1)"',
            'mkdir -p sub && cd sub && python3 -c "print(1)"',
            'cd /ws && ls -l && python3 -c "print(1)"',
            'python3 -c "print(1)" | tr a-z A-Z',
            "python3 audit.py | python3 -c 'import sys; print(len(sys.stdin.read()))'",
            'python3 -c "print(1)"; echo done',
            'python3 -c "print(1)" && echo ok',
            'python3 -c "print(1)" > out.txt',
            'python3 -c "print(1)" 2>/dev/null',
            "python3 -c 'import sys; print(sys.argv)' a b",
            "python3 -c 'print(1)' && python3 -c 'print(2)'",
            'FOO=bar python3 -c "print(1)"',
            'PYTHONPATH=. python3 -c "print(1)"',
            'export X=$(python3 -c "print(5)")',
            'x=$(python3 -c "print(1)"); echo $x',
            'time python3 -c "print(1)"',
            'timeout 30 python3 -c "print(1)"',
            'sudo -u x python3 -c "print(1)"',
            '/usr/bin/python3 -c "print(1)"',
            'env python3 -c "print(1)"',
            'uv run python -c "print(1)"',
            'python3 -u -c "print(1)"',
            'python3.12 -c "print(1)"',
            "ssh host 'python3 -c \"print(1)\"'",
            'docker exec c python3 -c "print(1)"',
        ],
    )
    def test_python_c_with_anything_else_around_it_is_a_shell_command(self, command):
        assert read_python_program(command) is None
        assert extract_python_from_bash(command) is None

    @pytest.mark.parametrize(
        "command",
        [
            'grep -rn "python3 -c" docs/',
            'git commit -m "fix python -c quoting"',
            "echo 'use python3 -c \"print(1)\"'",
            "cat > notes.md <<'EOF'\nrun python3 -c \"print(1)\"\nEOF",
        ],
    )
    def test_a_mention_of_python_c_is_not_a_program(self, command):
        assert read_python_program(command) is None

    @pytest.mark.parametrize(
        "command",
        [
            "python3 -c \"import os; print('$HOME')\"",
            "python3 -c \"print('`echo x`')\"",
            "python3 -c \"print('$(echo x)')\"",
        ],
        ids=["dollar", "backtick", "command-substitution"],
    )
    def test_a_payload_the_shell_would_expand_is_left_to_the_shell(self, command):
        assert read_python_program(command) is None

    def test_an_unquoted_payload_is_kept_for_the_parser_to_report(self):
        program = read_python_program("python3 -c print(1)")
        assert program is not None
        assert program.raw
        assert program.code == "print(1)"
        assert "raw fallback" in program.reason
        assert extract_python_from_bash("python3 -c print(1)") is None

    @pytest.mark.parametrize(
        ("command", "code"),
        [
            ("python3 -c \"print('a\\nb')\"", "print('a\\nb')"),
            ("python3 -c 'print(\"a\\nb\")'", 'print("a\\nb")'),
            ('python3 -c "print(\\"hi\\")"', 'print("hi")'),
            ("python3 -c \"print('a\\\\\\\\b')\"", "print('a\\\\b')"),
        ],
        ids=["dq-backslash-n", "sq-backslash-n", "dq-escaped-quote", "dq-double-backslash"],
    )
    def test_the_payload_is_the_text_the_shell_passes_on(self, command, code):
        """Quote removal is the shell's: ``\\n`` inside double quotes stays two characters, ``\\"`` becomes ``"``."""
        program = read_python_program(command)
        assert program is not None
        assert program.code == code

    @pytest.mark.parametrize(
        "command",
        [
            "python3 - <<'EOF'\nprint(1)\nEOF",
            "python3 <<'EOF'\nprint(1)\nEOF",
            "python3 - <<EOF\nprint(1)\nEOF",
            'python3 - <<"EOF"\nprint(1)\nEOF',
            "python3 - <<'EOF'\nprint(1)\nEOF\n",
            "cd /ws && python3 - <<'EOF'\nprint(1)\nEOF",
            "cd /ws\npython3 - <<'EOF'\nprint(1)\nEOF",
        ],
    )
    def test_python_heredoc_alone_is_a_program(self, command):
        program = read_python_program(command)
        assert program is not None
        assert program.code == "print(1)"
        assert program.wrapper is PythonWrapper.STDIN_HEREDOC

    @pytest.mark.parametrize(
        "command",
        [
            "mkdir -p out && python3 - <<'EOF'\nprint(1)\nEOF",
            "python3 - <<'EOF'\nprint(1)\nEOF\necho done",
            "python3 - <<'EOF' > out.txt\nprint(1)\nEOF",
            "make all > make.log 2>&1\necho EXIT=$?\npython3 - <<'EOF'\nprint(1)\nEOF",
        ],
    )
    def test_python_heredoc_with_other_commands_is_a_shell_command(self, command):
        assert read_python_program(command) is None

    def test_a_mention_of_python_c_inside_the_heredoc_body_does_not_change_the_program(self):
        program = read_python_program("python3 - <<'EOF'\nprint('python3 -c x')\nEOF")
        assert program is not None
        assert program.code == "print('python3 -c x')"
        assert program.wrapper is PythonWrapper.STDIN_HEREDOC

    @pytest.mark.parametrize(
        "command",
        [
            "python3 - <<EOF\nimport os\nprint('$HOME')\nEOF",
            "python3 - <<EOF\nprint('a\\\\b')\nEOF",
            "python3 - <<EOF\nprint(`date`)\nEOF",
            "python3 - <<-EOF\n\tprint(1)\nEOF",
            "cat > job.py <<EOF\nimport os\nprint('$HOME')\nEOF",
        ],
        ids=["dollar", "double-backslash", "backtick", "dash-tabs", "cat-dollar"],
    )
    def test_a_heredoc_the_shell_rewrites_before_python_reads_it_is_left_to_the_shell(self, command):
        assert read_python_program(command) is None

    @pytest.mark.parametrize(
        "command",
        [
            "python3 - <<'EOF'\nimport os\nprint('$HOME')\nEOF",
            "python3 - <<\"EOF\"\nimport os\nprint('$HOME')\nEOF",
            "python3 - <<EOF\nprint('a\\nb')\nEOF",
        ],
        ids=["quoted-marker", "double-quoted-marker", "backslash-n-is-not-consumed"],
    )
    def test_a_heredoc_the_shell_passes_on_unchanged_is_a_program(self, command):
        assert read_python_program(command) is not None

    @pytest.mark.parametrize(
        "command",
        [
            "cat > job.py <<'EOF'\nprint(1)\nEOF",
            "cd /ws && cat > job.py <<'EOF'\nprint(1)\nEOF",
            "cat > job.py <<'EOF'\nprint(1)\nEOF\npython3 job.py",
            "cat > job.py <<'EOF'\nprint(1)\nEOF\npython3 ./job.py",
            "cat > job.py <<'EOF'\nprint(1)\nEOF && python3 job.py",
        ],
    )
    def test_writing_a_python_file_alone_or_running_it_straight_after_is_a_program(self, command):
        program = read_python_program(command)
        assert program is not None
        assert program.code == "print(1)"
        assert program.wrapper is PythonWrapper.FILE_HEREDOC

    @pytest.mark.parametrize(
        "command",
        [
            "cat > job.py <<'EOF'\nprint(1)\nEOF\npython3 other.py",
            "cat > job.py <<'EOF'\nprint(1)\nEOF\npython3 job.py --flag",
            "cat > job.py <<'EOF'\nprint(1)\nEOF\nls job.py",
            "mkdir -p src && cat > src/job.py <<'EOF'\nprint(1)\nEOF",
            "cat > a.py <<'EOF'\nprint(1)\nEOF\ncat > b.py <<'EOF'\nprint(2)\nEOF",
        ],
    )
    def test_writing_a_python_file_with_other_commands_around_it_is_a_shell_command(self, command):
        assert read_python_program(command) is None

    def test_a_shell_command_is_no_program(self):
        assert read_python_program("ls -la") is None
        assert read_python_program("python3 script.py") is None


class TestPythonSourceThatImportsASkill:
    """Raw source reaches the skill runtime; shell text that only mentions a skill import does not."""

    @pytest.mark.parametrize(
        "command",
        [
            SKILL_CALL,
            "from skills.x_skill import y\ny()",
            "import skills.x_skill as s\ns.y()",
            "from tools.notify import send\nsend('x')",
            "# call it\nfrom skills.x_skill import y\ny()",
            "from skills.x_skill import y\nr = await y()\nprint(r)",
            "import os\nfrom skills.x_skill import y\nprint(os.getcwd())\ny()",
            "from skills.x_skill import y\ny(  ",
        ],
        ids=[
            "one-line",
            "two-lines",
            "bare-import",
            "tools",
            "comment-first",
            "await",
            "import-first",
            "typo-after-import",
        ],
    )
    def test_python_source_is_carried_as_it_is(self, command):
        assert extract_python_from_bash(command) == command

    @pytest.mark.parametrize(
        "command",
        [
            f'python3 -c "{SKILL_CALL}" && echo done',
            f'echo hi && python3 -c "{SKILL_CALL}"',
            f'python3 -c "{SKILL_CALL}" | head -5',
            f'python3 -c "{SKILL_CALL}"; echo done',
            f'/usr/bin/python3 -c "{SKILL_CALL}"',
            f'env python3 -c "{SKILL_CALL}"',
            f'uv run python -c "{SKILL_CALL}"',
            f'PYTHONPATH=. python3 -c "{SKILL_CALL}"',
            f'cd /ws && ls && python3 -c "{SKILL_CALL}"',
            f"python3 - <<'PY'\n{SKILL_CALL}\nPY\necho done",
            f"cat > j.py <<'EOF'\n{SKILL_CALL}\nEOF\npython3 j.py && echo done",
            'grep -rn "from skills.x_skill import" .',
            "printf 'from skills.x_skill import y\\ny()' | python3 -",
            "cat > notes.txt <<'EOF'\nfrom skills.x_skill import y\nEOF",
            "cat > notes.txt <<EOF\nfrom skills.x_skill import y\nEOF",
            "cat <<'EOF' > notes.txt\nfrom skills.x_skill import y\nEOF",
            "cat > notes.txt <<-EOF\nfrom skills.x_skill import y\nEOF",
            "cat broken\nfrom skills.daily_briefing_skill import run",
        ],
    )
    def test_a_shell_command_that_mentions_a_skill_import_carries_no_python(self, command):
        """The shell command goes to the shell whole; it is never run as Python source, nor reduced to a payload."""
        assert extract_python_from_bash(command) is None

    def test_source_that_does_not_open_like_python_is_left_to_the_shell_like_any_other_malformed_text(self):
        assert extract_python_from_bash("r = []\nfrom skills.x_skill import y\ny(  ") is None


class TestWorkIsBounded:
    """The reader runs on every command the bash tool receives, so no shape of command may make it stall."""

    # Far above the microseconds to milliseconds the reader needs, far below the stall a quadratic or exponential scan
    # of these shapes causes.
    BUDGET_SECONDS = 1.0

    @staticmethod
    def _read_timed(command: str) -> tuple[PythonProgram | None, float]:
        started = time.perf_counter()
        program = read_python_program(command)
        return program, time.perf_counter() - started

    def test_a_long_run_of_cd_lines_in_front_of_other_text_is_read_in_linear_time(self):
        program, seconds = self._read_timed(("cd a\n\n\n" * 18) + "X python3 -c 'print(1)'")
        assert program is None
        assert seconds < self.BUDGET_SECONDS

    def test_a_long_chain_of_cd_statements_is_read_in_linear_time(self):
        program, seconds = self._read_timed(("cd a && " * 2000) + "python3 -c 'print(1)'")
        assert program is not None
        assert program.code == "print(1)"
        assert seconds < self.BUDGET_SECONDS

    def test_a_run_of_blanks_after_a_written_file_is_read_in_linear_time(self):
        program, seconds = self._read_timed("cat > job.py <<'EOF'\nprint(1)\nEOF\n" + " " * 100_000 + "x")
        assert program is None
        assert seconds < self.BUDGET_SECONDS

    @pytest.mark.parametrize("opener", ["python3 <<A\nx\n", "cat > a.py <<A\nx\n"])
    def test_thousands_of_heredoc_openers_without_a_closing_marker_do_not_stall(self, opener):
        program, seconds = self._read_timed(opener * 10_000)
        assert program is None
        assert seconds < self.BUDGET_SECONDS

    def test_a_command_with_more_heredoc_openers_than_any_program_needs_is_a_shell_command(self):
        def command_with(openers: int) -> str:
            return "python3 <<'EOF'\nprint(1)\n" + "# <<\n" * (openers - 1) + "EOF"

        assert read_python_program(command_with(32)) is not None
        assert read_python_program(command_with(33)) is None

    def test_a_cd_without_a_target_is_no_cd_statement(self):
        """A bare ``cd`` goes home and the next line is a command of its own."""
        assert read_python_program("cd\n/ws\npython3 -c 'print(1)'") is None
