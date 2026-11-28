"""Tests for python_extractor — pipe-stdin surface and syntax validation."""

from myrm_agent_harness.toolkits.code_execution.python_extractor import (
    extract_python_from_pipe_stdin,
    validate_python_syntax,
)


class TestExtractPythonFromPipeStdin:
    def test_printf_pipe_python3(self):
        result = extract_python_from_pipe_stdin('printf "import myrm_tools" | python3')
        assert result == "import myrm_tools"

    def test_echo_skills_pipe_allowed_extraction(self):
        result = extract_python_from_pipe_stdin('echo "from skills.x import y" | python3')
        assert result == "from skills.x import y"

    def test_python_c_not_pipe_stdin_surface(self):
        assert extract_python_from_pipe_stdin('python -c "print(1)"') is None

    def test_pipe_to_grep_not_python_stdin(self):
        assert extract_python_from_pipe_stdin('echo "import myrm_tools" | grep x') is None

    def test_echo_unquoted_pipe_python3(self):
        result = extract_python_from_pipe_stdin("echo import myrm_tools | python3")
        assert result == "import myrm_tools"


class TestExtractCatPyPathsFromPipeFeeders:
    def test_cat_py_pipe_python3(self):
        from myrm_agent_harness.toolkits.code_execution.python_extractor import (
            extract_cat_py_paths_from_pipe_feeders,
        )

        paths = extract_cat_py_paths_from_pipe_feeders("cat /workspace/run.py | python3")
        assert paths == ["/workspace/run.py"]

    def test_cat_py_pipe_grep_not_extracted(self):
        from myrm_agent_harness.toolkits.code_execution.python_extractor import (
            extract_cat_py_paths_from_pipe_feeders,
        )

        assert extract_cat_py_paths_from_pipe_feeders("cat run.py | grep x") == []

    def test_python_c_not_cat_pipe_surface(self):
        from myrm_agent_harness.toolkits.code_execution.python_extractor import (
            extract_cat_py_paths_from_pipe_feeders,
        )

        assert extract_cat_py_paths_from_pipe_feeders('python3 -c "print(1)"') == []


class TestValidatePythonSyntax:
    def test_valid_code_returns_none(self):
        assert validate_python_syntax("print(1)") is None

    def test_top_level_await_syntax_allowed(self):
        code = "res = await fetch_data()\nprint(res)"
        assert validate_python_syntax(code) is None

    def test_top_level_async_for_allowed(self):
        code = "async for item in gen():\n    print(item)"
        assert validate_python_syntax(code) is None

    def test_top_level_async_with_allowed(self):
        code = "async with manager() as val:\n    print(val)"
        assert validate_python_syntax(code) is None

    def test_top_level_await_with_future_annotations_allowed(self):
        code = "from __future__ import annotations\n\nres = await fetch_data()\nprint(res)"
        assert validate_python_syntax(code) is None

    def test_top_level_async_comprehension_allowed(self):
        code = "vals = [i async for i in gen()]\nprint(vals)"
        assert validate_python_syntax(code) is None

    def test_top_level_return_outside_function_still_fails(self):
        code = "return 42"
        error = validate_python_syntax(code)
        assert error is not None
        assert "SyntaxError" in error

    def test_top_level_return_after_an_await_is_refused_like_one_before_it(self):
        after = validate_python_syntax("x = await fetch()\nreturn x")
        before = validate_python_syntax("return 1\nx = await fetch()")
        assert after is not None
        assert "'return' outside function" in after
        assert before is not None
        assert "'return' outside function" in before

    def test_top_level_await_with_actual_syntax_error_still_fails(self):
        code = "res = await fetch_data(\nprint(res)"
        error = validate_python_syntax(code)
        assert error is not None
        assert "SyntaxError" in error

    def test_invalid_code_returns_error_message(self):
        error = validate_python_syntax("def broken(")
        assert error is not None
        assert "SyntaxError" in error

    def test_invalid_code_includes_line_number(self):
        error = validate_python_syntax("x = \n def bad(")
        assert error is not None
        assert "line" in error

    def test_error_names_the_line_the_column_and_the_text_of_that_line(self):
        error = validate_python_syntax("import os\nvalue = (1, 2\nprint(value)\n")
        assert error == "SyntaxError: '(' was never closed, line 2, column 9: `value = (1, 2`"

    def test_indentation_of_the_line_is_kept_in_the_text(self):
        error = validate_python_syntax("def f():\n    return 1\n  x = 2\n")
        assert error is not None
        assert error.endswith(": `  x = 2`")

    def test_a_long_line_is_windowed_around_the_column(self):
        error = validate_python_syntax("y = [" + ", ".join(str(i) for i in range(100)) + " $ ]\n")
        assert error is not None
        assert "column 395" in error  # the `$` is far beyond the first 160 characters
        assert "99 $ ]`" in error
        assert error.split(": `", 1)[1].startswith("...")
        assert len(error) < 300

    def test_a_long_line_with_the_error_at_its_start_is_cut_at_the_end(self):
        error = validate_python_syntax("y = [$" + ", ".join(str(i) for i in range(200)) + "]\n")
        assert error is not None
        assert error.endswith("...`")
        assert ": `y = [$0, 1" in error

    def test_no_text_when_the_error_is_past_the_last_line(self):
        from myrm_agent_harness.toolkits.code_execution.python_extractor import _describe_syntax_error

        error = SyntaxError("unexpected EOF", ("<string>", 99, 1, "x"))
        assert _describe_syntax_error(error, "a = 1") == "SyntaxError: unexpected EOF, line 99, column 1"

    def test_form_feed_does_not_shift_the_reported_line(self):
        error = validate_python_syntax("a = 1\x0c\nb = (\nc = 3\n")
        assert error is not None
        assert "line 2" in error
        assert error.endswith("`b = (`")
