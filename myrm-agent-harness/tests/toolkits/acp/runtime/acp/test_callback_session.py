"""Tests for the ACP callback handler's session updates, file writes, offered capabilities and helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Self
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
    async def test_a_text_is_written_as_the_bytes_it_holds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Where text mode translates newlines (Windows), a text with CRLF and a lone CR must still land unchanged."""
        emulate_windows_text_mode(monkeypatch)
        text = "crlf\r\nlone\rcr\nunix\ncafé\n"
        target = tmp_path / "written.txt"

        await self._handler(tmp_path).write_text_file("s1", str(target), text)

        assert target.read_bytes() == text.encode()


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

    def test_resolve_safe_path_oserror_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from myrm_agent_harness.toolkits.acp.runtime.acp import callback as mod

        class _BoomPath(Path):
            def resolve(self, strict: bool = False) -> Self:
                raise OSError("too many symlinks")

        with monkeypatch.context() as m:
            # Force Path(...) inside _resolve_safe_path to hit OSError via resolve.
            m.setattr("myrm_agent_harness.toolkits.acp.runtime.acp.callback.Path", _BoomPath)
            assert mod._resolve_safe_path("file.txt", str(tmp_path)) is None
