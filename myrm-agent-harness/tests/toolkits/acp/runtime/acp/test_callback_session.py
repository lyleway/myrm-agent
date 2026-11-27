"""Tests for the ACP callback handler's session updates, file writes, offered capabilities and helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Self

import pytest
from acp import RequestError
from acp.schema import AgentMessageChunk, AgentThoughtChunk, TextContentBlock, ToolCallProgress, ToolCallStart

from myrm_agent_harness.toolkits.acp.runtime.acp.callback import AcpCallbackHandler
from myrm_agent_harness.toolkits.acp.types import PermissionMode, RuntimeConfig, RuntimeEvent, RuntimeEventType
from tests.support.windows_text_mode import emulate_windows_text_mode

_WORKSPACE = "/tmp/ws"


def _handler() -> AcpCallbackHandler:
    handler = AcpCallbackHandler(RuntimeConfig(backend_type="acp", command="dummy"), agent="mock", workspace=_WORKSPACE)
    handler.begin_turn(None, "s1")
    return handler


def _queued(handler: AcpCallbackHandler) -> list[RuntimeEvent]:
    events: list[RuntimeEvent] = []
    while not handler.event_queue.empty():
        queued = handler.event_queue.get_nowait()
        assert queued is not None
        events.append(queued)
    return events


def _texts(handler: AcpCallbackHandler) -> list[str]:
    return [str(event.data["content"]) for event in _queued(handler) if event.type is RuntimeEventType.TEXT_DELTA]


def _said(text: str) -> AgentMessageChunk:
    return AgentMessageChunk(content=TextContentBlock(text=text))


def _called(call_id: str = "t1", title: str = "bash") -> ToolCallStart:
    return ToolCallStart(tool_call_id=call_id, title=title, kind="execute")


class TestAcpCallbackHandlerSessionUpdate:
    async def test_an_agent_message_is_text_of_the_turn_the_host_named(self) -> None:
        handler = _handler()

        await handler.session_update("agent-session", _said("hello world"))

        (event,) = _queued(handler)
        assert (event.type, event.data["content"], event.session_id) == (
            RuntimeEventType.TEXT_DELTA,
            "hello world",
            "s1",
        )

    async def test_a_tool_call_starts_and_finishes_as_tool_events(self) -> None:
        handler = _handler()

        await handler.session_update("agent-session", _called("tc1"))
        await handler.session_update("agent-session", ToolCallProgress(tool_call_id="tc1", status="completed"))

        start, result = _queued(handler)
        assert (start.type, start.data["tool_name"], start.data["tool_call_id"]) == (
            RuntimeEventType.TOOL_START,
            "bash",
            "tc1",
        )
        assert (result.type, result.data["tool_call_id"], result.data["is_error"]) == (
            RuntimeEventType.TOOL_RESULT,
            "tc1",
            False,
        )

    async def test_a_failed_tool_call_is_a_failed_result(self) -> None:
        handler = _handler()

        await handler.session_update("agent-session", ToolCallProgress(tool_call_id="tc1", status="failed"))

        (result,) = _queued(handler)
        assert result.data["is_error"] is True

    async def test_a_thought_is_reasoning(self) -> None:
        handler = _handler()

        await handler.session_update("agent-session", AgentThoughtChunk(content=TextContentBlock(text="thinking...")))

        (event,) = _queued(handler)
        assert (event.type, event.data["content"]) == (RuntimeEventType.REASONING_DELTA, "thinking...")

    async def test_a_new_turn_drops_what_an_earlier_one_left_queued(self) -> None:
        handler = _handler()
        await handler.session_update("agent-session", _said("left over"))

        handler.begin_turn(None, "s2")

        assert _queued(handler) == []

    async def test_what_the_agent_sent_before_the_turn_began_is_not_its_output(self) -> None:
        """The history a loaded session replays arrives before the turn, and is dropped when the turn begins."""
        handler = _handler()
        await handler.session_update("agent-session", _said("replayed history"))

        handler.begin_turn(None, "chat-1")
        await handler.session_update("agent-session", _said("new"))

        assert _texts(handler) == ["new"]


class TestMessagesAroundAToolCall:
    async def test_the_text_after_a_tool_call_is_set_apart_from_the_text_before_it(self) -> None:
        handler = _handler()

        await handler.session_update("a", _said("Let me look."))
        await handler.session_update("a", _called())
        await handler.session_update("a", _said("Found it."))

        assert _texts(handler) == ["Let me look.", "\n\nFound it."]

    async def test_chunks_of_one_message_are_not_broken_up(self) -> None:
        handler = _handler()

        await handler.session_update("a", _said("Let me "))
        await handler.session_update("a", _said("look."))

        assert _texts(handler) == ["Let me ", "look."]

    async def test_the_newlines_the_agent_wrote_count_towards_the_break(self) -> None:
        handler = _handler()

        await handler.session_update("a", _said("one\n"))
        await handler.session_update("a", _called("t1"))
        await handler.session_update("a", _said("two"))
        await handler.session_update("a", _called("t2"))
        await handler.session_update("a", _said("\n\nthree"))

        assert _texts(handler) == ["one\n", "\ntwo", "\n\nthree"]

    async def test_several_tool_calls_in_a_row_make_one_break(self) -> None:
        handler = _handler()

        await handler.session_update("a", _said("before"))
        await handler.session_update("a", _called("t1"))
        await handler.session_update("a", _called("t2"))
        await handler.session_update("a", _said("after"))

        assert _texts(handler) == ["before", "\n\nafter"]

    async def test_text_with_nothing_before_it_is_left_alone(self) -> None:
        handler = _handler()

        await handler.session_update("a", _called())
        await handler.session_update("a", _said("first words"))

        assert _texts(handler) == ["first words"]

    async def test_a_new_turn_does_not_continue_the_text_of_the_one_before(self) -> None:
        handler = _handler()
        await handler.session_update("a", _said("earlier turn"))
        handler.begin_turn(None, "s2")

        await handler.session_update("a", _called())
        await handler.session_update("a", _said("fresh start"))

        assert _texts(handler) == ["fresh start"]


class TestAcpCallbackHandlerFileOps:
    @staticmethod
    def _handler(workspace: Path, mode: PermissionMode = "allow_all") -> AcpCallbackHandler:
        return AcpCallbackHandler(
            RuntimeConfig(backend_type="acp", command="dummy", permission_mode=mode),
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
