"""Tests for the Auto-Verify diagnostic parsing, filtering and formatting."""

from __future__ import annotations

from myrm_agent_harness.agent.meta_tools.file_ops.validators.auto_verify import (
    Diagnostic,
    _filter_diagnostics,
    _format_diagnostics,
    _parse_pyright_output,
    _parse_pyright_text_output,
)


class TestParsePyrightOutput:
    def test_valid_json_output(self) -> None:
        raw = """{
            "generalDiagnostics": [
                {
                    "file": "/workspace/src/main.py",
                    "severity": "error",
                    "message": "Cannot access attribute 'namee' on type 'User'",
                    "range": {"start": {"line": 4, "character": 11}}
                }
            ]
        }"""
        result = _parse_pyright_output(raw)
        assert len(result) == 1
        assert result[0].file == "/workspace/src/main.py"
        assert result[0].line == 5
        assert result[0].col == 12
        assert result[0].severity == "error"
        assert "namee" in result[0].message

    def test_filters_warnings(self) -> None:
        raw = """{
            "generalDiagnostics": [
                {
                    "file": "/workspace/src/main.py",
                    "severity": "warning",
                    "message": "Variable is unused",
                    "range": {"start": {"line": 1, "character": 0}}
                },
                {
                    "file": "/workspace/src/main.py",
                    "severity": "error",
                    "message": "Type error",
                    "range": {"start": {"line": 2, "character": 5}}
                }
            ]
        }"""
        result = _parse_pyright_output(raw)
        assert len(result) == 1
        assert result[0].severity == "error"

    def test_invalid_json_falls_back_to_text_output(self) -> None:
        raw = "src/main.py:5:12 - error: Type 'str' is not assignable"
        result = _parse_pyright_output(raw)
        assert len(result) == 1

    def test_empty_diagnostics(self) -> None:
        raw = '{"generalDiagnostics": []}'
        result = _parse_pyright_output(raw)
        assert len(result) == 0

    def test_missing_range_fields_uses_defaults(self) -> None:
        """Pyright diag with missing range/start fields defaults to line=1, col=1."""
        raw = '{"generalDiagnostics": [{"file": "x.py", "severity": "error", "message": "err"}]}'
        result = _parse_pyright_output(raw)
        assert len(result) == 1
        assert result[0].line == 1
        assert result[0].col == 1

    def test_missing_file_field_uses_empty(self) -> None:
        """Pyright diag missing file field defaults to empty string."""
        raw = '{"generalDiagnostics": [{"severity": "error", "message": "err", "range": {"start": {"line": 0, "character": 0}}}]}'
        result = _parse_pyright_output(raw)
        assert len(result) == 1
        assert result[0].file == ""


class TestParsePyrightTextOutput:
    def test_text_format(self) -> None:
        raw = "src/main.py:5:12 - error: Cannot access attribute"
        result = _parse_pyright_text_output(raw)
        assert len(result) == 1
        assert result[0].file == "src/main.py"
        assert result[0].line == 5
        assert result[0].col == 12
        assert "Cannot access attribute" in result[0].message

    def test_multiple_errors(self) -> None:
        raw = "src/a.py:1:1 - error: First error\nsrc/b.py:2:3 - error: Second error\n"
        result = _parse_pyright_text_output(raw)
        assert len(result) == 2

    def test_ignores_non_error_lines(self) -> None:
        raw = "Compiling...\nDone. No errors found."
        result = _parse_pyright_text_output(raw)
        assert len(result) == 0

    def test_tsc_style_lines_are_not_diagnostics(self) -> None:
        raw = "src/app.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'."
        assert _parse_pyright_text_output(raw) == []


class TestFilterDiagnostics:
    def test_filters_by_file(self) -> None:
        diags = [
            Diagnostic(file="src/main.py", line=5, col=1, severity="error", message="err"),
            Diagnostic(file="src/other.py", line=3, col=1, severity="error", message="err"),
        ]
        result = _filter_diagnostics(diags, "src/main.py", None, None)
        assert len(result) == 1
        assert result[0].file == "src/main.py"

    def test_filters_by_edit_range(self) -> None:
        diags = [
            Diagnostic(file="main.py", line=5, col=1, severity="error", message="near edit"),
            Diagnostic(file="main.py", line=100, col=1, severity="error", message="far away"),
        ]
        result = _filter_diagnostics(diags, "main.py", 3, 7)
        assert len(result) == 1
        assert result[0].line == 5

    def test_margin_of_10_lines(self) -> None:
        diags = [
            Diagnostic(file="main.py", line=20, col=1, severity="error", message="within margin"),
        ]
        result = _filter_diagnostics(diags, "main.py", 15, 15)
        assert len(result) == 1

    def test_respects_max_limit(self) -> None:
        diags = [Diagnostic(file="main.py", line=i, col=1, severity="error", message=f"err {i}") for i in range(1, 20)]
        result = _filter_diagnostics(diags, "main.py", None, None)
        assert len(result) == 5

    def test_no_range_reports_all_errors_in_file(self) -> None:
        diags = [
            Diagnostic(file="main.py", line=1, col=1, severity="error", message="e1"),
            Diagnostic(file="main.py", line=50, col=1, severity="error", message="e2"),
            Diagnostic(file="main.py", line=100, col=1, severity="error", message="e3"),
        ]
        result = _filter_diagnostics(diags, "main.py", None, None)
        assert len(result) == 3

    def test_excludes_non_error_severity(self) -> None:
        """Non-error severities are excluded even if in range."""
        diags = [
            Diagnostic(file="main.py", line=5, col=1, severity="warning", message="w"),
            Diagnostic(file="main.py", line=5, col=1, severity="information", message="i"),
            Diagnostic(file="main.py", line=5, col=1, severity="error", message="e"),
        ]
        result = _filter_diagnostics(diags, "main.py", None, None)
        assert len(result) == 1
        assert result[0].severity == "error"

    def test_endswith_path_matching(self) -> None:
        """Diagnostics with absolute paths match when file_path is relative suffix."""
        diags = [
            Diagnostic(file="/home/user/project/src/main.py", line=1, col=1, severity="error", message="e"),
        ]
        result = _filter_diagnostics(diags, "src/main.py", None, None)
        assert len(result) == 1

    def test_reverse_endswith_path_matching(self) -> None:
        """file_path is absolute, diagnostic has relative path."""
        diags = [
            Diagnostic(file="src/main.py", line=1, col=1, severity="error", message="e"),
        ]
        result = _filter_diagnostics(diags, "/home/user/project/src/main.py", None, None)
        assert len(result) == 1


class TestFormatDiagnostics:
    def test_basic_format(self) -> None:
        diags = [
            Diagnostic(file="src/main.py", line=5, col=12, severity="error", message="Type mismatch"),
        ]
        result = _format_diagnostics(diags)
        assert "src/main.py:5:12" in result
        assert "error" in result
        assert "Type mismatch" in result
