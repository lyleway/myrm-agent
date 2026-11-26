"""Tests for the ACP callback handler's session updates, file operations and helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from acp import RequestError

from myrm_agent_harness.toolkits.acp.runtime.acp.callback import AcpCallbackHandler
from myrm_agent_harness.toolkits.acp.types import PermissionMode, RuntimeConfig
from tests.support.windows_text_mode import emulate_windows_text_mode

_WORKSPACE = "/tmp/ws"


class TestAcpCallbackHandlerSessionUpdate:
    @pytest.mark.asyncio
    async def test_agent_message_chunk_text(self) -> None:
        handler = AcpCallbackHandler(
            RuntimeConfig(backend_type="acp", command="dummy"),
            "s1",
            agent="mock",
            workspace=_WORKSPACE,
        )
        update = MagicMock()
        update.session_update = "agent_message_chunk"
        update.sessionUpdate = None
        update.content = MagicMock()
        update.content.type = "text"
        update.content.text = "hello world"
        await handler.session_update("s1", update)
        assert "hello world" in handler.response_text

    @pytest.mark.asyncio
    async def test_tool_call_update(self) -> None:
        handler = AcpCallbackHandler(
            RuntimeConfig(backend_type="acp", command="dummy"),
            "s1",
            agent="mock",
            workspace=_WORKSPACE,
        )
        update = MagicMock()
        update.session_update = "tool_call"
        update.sessionUpdate = None
        update.title = "bash"
        update.status = "running"
        update.content = None
        await handler.session_update("s1", update)
        assert "bash" in handler.response_text

    @pytest.mark.asyncio
    async def test_tool_call_update_completed(self) -> None:
        handler = AcpCallbackHandler(
            RuntimeConfig(backend_type="acp", command="dummy"),
            "s1",
            agent="mock",
            workspace=_WORKSPACE,
        )
        update = MagicMock()
        update.session_update = "tool_call_update"
        update.sessionUpdate = None
        update.status = "completed"
        update.tool_call_id = "tc1"
        update.toolCallId = None
        update.content = None
        await handler.session_update("s1", update)
        assert "tc1" in handler.response_text

    @pytest.mark.asyncio
    async def test_agent_thought_chunk(self) -> None:
        handler = AcpCallbackHandler(
            RuntimeConfig(backend_type="acp", command="dummy"),
            "s1",
            agent="mock",
            workspace=_WORKSPACE,
        )
        update = MagicMock()
        update.session_update = "agent_thought_chunk"
        update.sessionUpdate = None
        update.content = MagicMock()
        update.content.type = "text"
        update.content.text = "thinking..."
        await handler.session_update("s1", update)
        assert "thinking..." in handler.response_text

    @pytest.mark.asyncio
    async def test_reset_clears_text(self) -> None:
        handler = AcpCallbackHandler(
            RuntimeConfig(backend_type="acp", command="dummy"),
            "s1",
            agent="mock",
            workspace=_WORKSPACE,
        )
        handler._text_parts.append("old text")
        handler.reset()
        assert handler.response_text == ""


class TestAcpCallbackHandlerFileOps:
    @staticmethod
    def _handler(workspace: Path, mode: PermissionMode = "allow_all") -> AcpCallbackHandler:
        return AcpCallbackHandler(
            RuntimeConfig(backend_type="acp", command="dummy", permission_mode=mode),
            "s1",
            agent="mock",
            workspace=str(workspace),
        )

    @pytest.mark.asyncio
    async def test_read_text_file_within_cwd(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.txt"
        test_file.write_text("file content", encoding="utf-8")

        result = await self._handler(tmp_path).read_text_file("s1", str(test_file))

        assert result.content == "file content"

    @pytest.mark.asyncio
    async def test_read_text_file_with_line_range(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = await self._handler(tmp_path).read_text_file("s1", str(test_file), line=2, limit=1)

        assert result.content == "line2\n"

    @pytest.mark.asyncio
    async def test_read_text_file_outside_cwd_is_an_invalid_params_error(self, tmp_path: Path) -> None:
        with pytest.raises(RequestError) as error:
            await self._handler(tmp_path / "sub").read_text_file("s1", "/etc/passwd")

        assert error.value.code == -32602
        assert error.value.data == {"path": "/etc/passwd", "reason": "the path is outside the workspace"}

    @pytest.mark.asyncio
    async def test_read_text_file_nonexistent_is_a_resource_not_found_error(self, tmp_path: Path) -> None:
        with pytest.raises(RequestError) as error:
            await self._handler(tmp_path).read_text_file("s1", str(tmp_path / "nope.txt"))

        assert error.value.code == -32002

    @pytest.mark.asyncio
    async def test_read_text_file_that_is_not_utf8_is_an_error_not_a_lossy_read(self, tmp_path: Path) -> None:
        (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00bad")

        with pytest.raises(RequestError) as error:
            await self._handler(tmp_path).read_text_file("s1", "blob.bin")

        assert error.value.code == -32602
        assert error.value.data == {"path": "blob.bin", "reason": "the file is not UTF-8 text"}

    @pytest.mark.asyncio
    async def test_read_text_file_that_cannot_be_opened_is_an_internal_error(self, tmp_path: Path) -> None:
        (tmp_path / "directory.txt").mkdir()

        with pytest.raises(RequestError) as error:
            await self._handler(tmp_path).read_text_file("s1", "directory.txt")

        assert error.value.code == -32603

    @pytest.mark.asyncio
    async def test_write_text_file_allow_all(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "output.txt"

        await self._handler(tmp_path).write_text_file("s1", str(target), "content")

        assert target.read_text() == "content"

    @pytest.mark.asyncio
    async def test_write_text_file_safe_mode_without_a_broker_is_a_permission_error(self, tmp_path: Path) -> None:
        with pytest.raises(RequestError) as error:
            await self._handler(tmp_path, "safe").write_text_file("s1", str(tmp_path / "out.txt"), "content")

        assert error.value.code == -32001
        assert not (tmp_path / "out.txt").exists()

    @pytest.mark.asyncio
    async def test_write_text_file_outside_cwd_is_an_invalid_params_error(self, tmp_path: Path) -> None:
        with pytest.raises(RequestError) as error:
            await self._handler(tmp_path).write_text_file("s1", "/tmp/evil.txt", "content")

        assert error.value.code == -32602

    @pytest.mark.asyncio
    async def test_write_text_file_that_cannot_be_written_is_an_internal_error(self, tmp_path: Path) -> None:
        # A path whose parent is an existing file forces mkdir → FileExistsError/OSError.
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file")

        with pytest.raises(RequestError) as error:
            await self._handler(tmp_path).write_text_file("s1", str(blocker / "sub" / "out.txt"), "content")

        assert error.value.code == -32603

    @pytest.mark.asyncio
    async def test_a_file_is_read_with_its_line_endings(self, tmp_path: Path) -> None:
        target = tmp_path / "crlf.txt"
        target.write_bytes(b"one\r\ntwo\r\nthree\r\n")
        handler = self._handler(tmp_path)

        whole = await handler.read_text_file("s1", str(target))
        second_line = await handler.read_text_file("s1", str(target), line=2, limit=1)

        assert whole.content == "one\r\ntwo\r\nthree\r\n"
        assert second_line.content == "two\r\n"

    @pytest.mark.asyncio
    async def test_a_file_read_and_written_back_is_unchanged(self, tmp_path: Path) -> None:
        """An agent that edits nothing must not change a byte: CRLF, a lone CR and non-ASCII text all survive."""
        original = "crlf\r\nlone\rcr\nunix\ncafé\r\n".encode()
        target = tmp_path / "mixed.txt"
        target.write_bytes(original)
        handler = self._handler(tmp_path)

        read = await handler.read_text_file("s1", str(target))
        await handler.write_text_file("s1", str(target), read.content)

        assert target.read_bytes() == original

    @pytest.mark.asyncio
    async def test_a_text_is_written_as_the_bytes_it_holds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Where text mode translates newlines (Windows), a text with CRLF and a lone CR must still land unchanged."""
        emulate_windows_text_mode(monkeypatch)
        text = "crlf\r\nlone\rcr\nunix\ncafé\n"
        target = tmp_path / "written.txt"

        await self._handler(tmp_path).write_text_file("s1", str(target), text)

        assert target.read_bytes() == text.encode()

    @pytest.mark.parametrize(
        ("line", "limit", "expected"),
        [
            (None, None, "a\r\nb\rc\nd"),
            (2, 2, "b\rc\n"),
            (3, None, "c\nd"),
            (None, 1, "a\r\n"),
            (1, 0, ""),
            (0, 1, "a\r\n"),
            (-5, 1, "a\r\n"),
            (99, 1, ""),
        ],
    )
    @pytest.mark.asyncio
    async def test_lines_end_at_cr_lf_or_crlf_and_line_and_limit_are_independent(
        self, tmp_path: Path, line: int | None, limit: int | None, expected: str
    ) -> None:
        (tmp_path / "mixed.txt").write_bytes(b"a\r\nb\rc\nd")

        result = await self._handler(tmp_path).read_text_file("s1", "mixed.txt", line=line, limit=limit)

        assert result.content == expected


class TestAcpCallbackHelpers:
    """Pure helper boundaries: text extraction and safe-path resolution."""

    def test_extract_text_content_none(self) -> None:
        from myrm_agent_harness.toolkits.acp.runtime.acp import callback as mod

        assert mod._extract_text_content(None) is None

    def test_extract_text_content_non_text_type(self) -> None:
        from myrm_agent_harness.toolkits.acp.runtime.acp import callback as mod

        class _Other:
            type = "image"

        assert mod._extract_text_content(_Other()) is None

    def test_extract_text_content_text_no_text_field(self) -> None:
        from myrm_agent_harness.toolkits.acp.runtime.acp import callback as mod

        class _Text:
            type = "text"

        assert mod._extract_text_content(_Text()) is None

    def test_resolve_safe_path_inside_cwd(self, tmp_path: Path) -> None:
        from myrm_agent_harness.toolkits.acp.runtime.acp import callback as mod

        resolved = mod._resolve_safe_path("sub/file.txt", str(tmp_path))
        assert resolved is not None
        assert str(resolved).startswith(str(tmp_path))

    def test_resolve_safe_path_escape_returns_none(self, tmp_path: Path) -> None:
        from myrm_agent_harness.toolkits.acp.runtime.acp import callback as mod

        assert mod._resolve_safe_path("../evil.txt", str(tmp_path)) is None
        assert mod._resolve_safe_path("/etc/passwd", str(tmp_path)) is None

    def test_resolve_safe_path_oserror_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        from myrm_agent_harness.toolkits.acp.runtime.acp import callback as mod

        class _BoomPath(Path):
            def resolve(self, strict: bool = False) -> Path:
                raise OSError("too many symlinks")

        with monkeypatch.context() as m:
            # Force Path(...) inside _resolve_safe_path to hit OSError via resolve.
            m.setattr("myrm_agent_harness.toolkits.acp.runtime.acp.callback.Path", _BoomPath)
            assert mod._resolve_safe_path("file.txt", str(tmp_path)) is None
