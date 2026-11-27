"""Tests for the Auto-Verify linter registry and the ``run_auto_verify`` flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from myrm_agent_harness.agent.meta_tools.file_ops.validators.auto_verify import (
    AUTO_VERIFY_TIMEOUT_SECONDS,
    LINTER_REGISTRY,
    _reset_cache,
    run_auto_verify,
)
from myrm_agent_harness.toolkits.code_execution.executors.models import ExecutionResult


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear linter availability cache before each test."""
    _reset_cache()
    yield
    _reset_cache()


class TestLinterRegistry:
    def test_python_extension_registered(self):
        assert ".py" in LINTER_REGISTRY
        assert "pyright" in LINTER_REGISTRY[".py"].command_template

    def test_only_python_is_registered(self):
        """A per-file TypeScript, Go or Rust check is silent or reports phantom errors, so none is registered."""
        assert set(LINTER_REGISTRY) == {".py"}

    def test_unsupported_extension(self):
        assert ".md" not in LINTER_REGISTRY
        assert ".txt" not in LINTER_REGISTRY
        assert ".html" not in LINTER_REGISTRY


class TestRunAutoVerify:
    @pytest.mark.asyncio
    async def test_unsupported_extension_returns_none(self):
        executor = AsyncMock()
        result = await run_auto_verify(executor, "/workspace/readme.md")
        assert result is None
        executor.execute_bash.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["a.ts", "a.tsx", "a.go", "a.rs", "a.js"])
    async def test_languages_without_a_registered_linter_start_no_process(self, name):
        executor = AsyncMock()
        result = await run_auto_verify(executor, f"/workspace/{name}")
        assert result is None
        executor.execute_bash.assert_not_called()

    @pytest.mark.asyncio
    async def test_linter_not_available_returns_none(self):
        executor = AsyncMock()
        exec_result = AsyncMock()
        exec_result.success = False
        executor.execute_bash.return_value = exec_result

        result = await run_auto_verify(executor, "/workspace/main.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_linter_available_no_errors_returns_none(self):
        executor = AsyncMock()

        which_result = AsyncMock()
        which_result.success = True

        lint_result = AsyncMock()
        lint_result.success = True
        lint_result.stdout = ""
        lint_result.stderr = ""

        executor.execute_bash.side_effect = [which_result, lint_result]

        result = await run_auto_verify(executor, "/workspace/main.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_linter_finds_errors(self):
        executor = AsyncMock()

        which_result = AsyncMock()
        which_result.success = True

        lint_result = AsyncMock()
        lint_result.success = False
        lint_result.stdout = '{"generalDiagnostics": [{"file": "/workspace/main.py", "severity": "error", "message": "Type mismatch", "range": {"start": {"line": 4, "character": 0}}}]}'
        lint_result.stderr = ""

        executor.execute_bash.side_effect = [which_result, lint_result]

        result = await run_auto_verify(executor, "/workspace/main.py")
        assert result is not None
        assert "[Auto-Verify]" in result
        assert "Type mismatch" in result

    @pytest.mark.asyncio
    async def test_uses_cache_for_availability(self):
        executor = AsyncMock()

        which_result = AsyncMock()
        which_result.success = True

        lint_result = AsyncMock()
        lint_result.success = True
        lint_result.stdout = ""
        lint_result.stderr = ""

        executor.execute_bash.side_effect = [which_result, lint_result, lint_result]

        await run_auto_verify(executor, "/workspace/a.py")
        await run_auto_verify(executor, "/workspace/b.py")

        # 'which pyright' should only be called once (cached)
        calls = executor.execute_bash.call_args_list
        which_calls = [c for c in calls if "which" in str(c)]
        assert len(which_calls) == 1

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        executor = AsyncMock()

        which_result = AsyncMock()
        which_result.success = True

        executor.execute_bash.side_effect = [
            which_result,
            TimeoutError("timed out"),
        ]

        result = await run_auto_verify(executor, "/workspace/main.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_edit_range_filters_diagnostics(self):
        """Verify edit_line_start/end params trigger incremental filtering."""
        executor = AsyncMock()

        which_result = AsyncMock()
        which_result.success = True

        lint_result = AsyncMock()
        lint_result.success = False
        lint_result.stdout = '{"generalDiagnostics": [{"file": "/workspace/main.py", "severity": "error", "message": "Near edit", "range": {"start": {"line": 9, "character": 0}}}, {"file": "/workspace/main.py", "severity": "error", "message": "Far away", "range": {"start": {"line": 99, "character": 0}}}]}'
        lint_result.stderr = ""

        executor.execute_bash.side_effect = [which_result, lint_result]

        result = await run_auto_verify(executor, "/workspace/main.py", edit_line_start=8, edit_line_end=12)
        assert result is not None
        assert "Near edit" in result
        assert "Far away" not in result

    @pytest.mark.asyncio
    async def test_pyright_invalid_json_falls_back_to_text_output(self):
        """When pyright outputs non-JSON, its text format is parsed instead."""
        executor = AsyncMock()

        which_result = AsyncMock()
        which_result.success = True

        lint_result = AsyncMock()
        lint_result.success = False
        lint_result.stdout = "/workspace/main.py:5:3 - error: Undefined variable 'foo'"
        lint_result.stderr = ""

        executor.execute_bash.side_effect = [which_result, lint_result]

        result = await run_auto_verify(executor, "/workspace/main.py")
        assert result is not None
        assert "Undefined variable" in result

    @pytest.mark.asyncio
    async def test_executor_generic_exception_returns_none(self):
        """Any non-timeout exception from executor is handled gracefully."""
        executor = AsyncMock()

        which_result = AsyncMock()
        which_result.success = True

        executor.execute_bash.side_effect = [
            which_result,
            RuntimeError("Connection reset"),
        ]

        result = await run_auto_verify(executor, "/workspace/main.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_executor_returns_none_early(self):
        """Unsupported extension still returns None even if executor is weird."""
        executor = AsyncMock()
        result = await run_auto_verify(executor, "/workspace/file.xyz")
        assert result is None
        executor.execute_bash.assert_not_called()

    @pytest.mark.asyncio
    async def test_linter_unavailable_cached_on_second_call(self):
        """Second call for same linter uses cached False result."""
        executor = AsyncMock()

        which_result = AsyncMock()
        which_result.success = False
        executor.execute_bash.return_value = which_result

        result1 = await run_auto_verify(executor, "/workspace/a.py")
        assert result1 is None

        result2 = await run_auto_verify(executor, "/workspace/b.py")
        assert result2 is None

        which_calls = [c for c in executor.execute_bash.call_args_list if "which" in str(c)]
        assert len(which_calls) == 1

    @pytest.mark.asyncio
    async def test_check_available_exception_returns_false(self):
        """Exception during `which` check marks linter as unavailable."""
        executor = AsyncMock()
        executor.execute_bash.side_effect = OSError("broken pipe")

        result = await run_auto_verify(executor, "/workspace/main.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_diagnostics_all_outside_edit_range_returns_none(self):
        """All diagnostics outside edit range => filtered to empty => return None."""
        executor = AsyncMock()

        which_result = AsyncMock()
        which_result.success = True

        lint_result = AsyncMock()
        lint_result.success = False
        lint_result.stdout = '{"generalDiagnostics": [{"file": "/workspace/main.py", "severity": "error", "message": "Far error", "range": {"start": {"line": 99, "character": 0}}}]}'
        lint_result.stderr = ""

        executor.execute_bash.side_effect = [which_result, lint_result]

        result = await run_auto_verify(executor, "/workspace/main.py", edit_line_start=1, edit_line_end=3)
        assert result is None

    @pytest.mark.asyncio
    async def test_unparseable_linter_output_returns_none(self):
        """Linter fails but output is garbage => no parseable diagnostics => None."""
        executor = AsyncMock()

        which_result = AsyncMock()
        which_result.success = True

        lint_result = AsyncMock()
        lint_result.success = False
        lint_result.stdout = "Some random garbage output\nSegfault at 0x0"
        lint_result.stderr = ""

        executor.execute_bash.side_effect = [which_result, lint_result]

        result = await run_auto_verify(executor, "/workspace/main.py")
        assert result is None


class TestWorkspaceContext:
    """The detection and the linter run from the workspace root, not from the directory of the host process."""

    @pytest.mark.asyncio
    async def test_detection_and_lint_run_from_the_workspace_root(self):
        executor = AsyncMock()
        executor.workspace_path = "/srv/ws"
        executor.execute_bash.return_value = ExecutionResult(success=True, exit_code=0)

        await run_auto_verify(executor, "/srv/ws/a.py")

        contexts = [call.args[0] for call in executor.execute_bash.await_args_list]
        assert [context.code for context in contexts] == ["which pyright", "pyright --outputjson /srv/ws/a.py"]
        assert [context.timeout for context in contexts] == [5, AUTO_VERIFY_TIMEOUT_SECONDS]
        for context in contexts:
            assert context.work_dir == "/workspace"
            assert context.enter_work_dir is True
            assert context.workspace_root == "/srv/ws"
