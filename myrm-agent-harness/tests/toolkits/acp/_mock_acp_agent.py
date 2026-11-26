"""Minimal ACP agent for end-to-end testing.

Run as a subprocess: python tests/toolkits/acp/_mock_acp_agent.py
Implements the acp.Agent protocol over stdin/stdout, echoing prompts back
with a prefix to verify the full ACP roundtrip. With ``--images`` it advertises
``promptCapabilities.image`` and reports every image block it received as
``[images] <mimeType>:<base64 length>:<sha256 prefix>``.

A prompt that starts with ``/permission``, ``/write``, ``/read`` or ``/edit`` followed by a JSON object makes the agent call
the client the way a real agent does, over the real SDK, and report what came back:
``/permission {"kind", "title", "raw_input", "locations", "diff_path"}`` -> ``[permission] <outcome> <option id>``;
``/write {"path", "content"}`` -> ``[write] ok`` or ``[write] error <code> <message> <data as JSON>``;
``/read {"path", "line", "limit"}`` -> ``[read] <content as JSON>`` or ``[read] error <code> <message> <data as JSON>``;
``/edit {"path", "content"}`` asks permission to edit the file, then writes it: both reports, joined.
``/argv`` -> ``[argv] <the arguments the agent was started with, as JSON>``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import uuid
from typing import Any

import acp
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    CloseSessionResponse,
    FileEditToolCallContent,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    NewSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    TextContentBlock,
    ToolCallLocation,
    ToolCallUpdate,
)

_OPTIONS = [
    PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
    PermissionOption(option_id="allow-always", name="Allow always", kind="allow_always"),
    PermissionOption(option_id="reject-once", name="Reject once", kind="reject_once"),
    PermissionOption(option_id="reject-always", name="Reject always", kind="reject_always"),
]


def _failure(verb: str, error: acp.RequestError) -> str:
    """How a JSON-RPC error from the client reads in a report: the code, the message and the error's data."""
    return f"[{verb}] error {error.code} {error} {json.dumps(error.data)}"


class MockAcpAgent:
    """Echo agent that mirrors prompts back through ACP protocol."""

    def __init__(self) -> None:
        self._conn: object | None = None
        self._sessions: dict[str, str] = {}

    def on_connect(self, conn: object) -> None:
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: object | None = None,
        client_info: object | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        advertise_images = "--images" in sys.argv[1:]
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(prompt_capabilities=PromptCapabilities(image=advertise_images)),
            agent_info=Implementation(name="mock-acp-agent", version="1.0.0"),
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[object] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = cwd
        return NewSessionResponse(session_id=session_id)

    async def load_session(self, cwd: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def list_sessions(self, **kwargs: Any) -> ListSessionsResponse:
        return ListSessionsResponse(sessions=[])

    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def set_session_model(self, model_id: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def set_config_option(self, config_id: str, session_id: str, value: str | bool, **kwargs: Any) -> None:
        return None

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        return None

    async def prompt(
        self,
        prompt: list[TextContentBlock | ImageContentBlock],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        text_parts = []
        for block in prompt:
            if hasattr(block, "text"):
                text_parts.append(block.text)
            elif isinstance(block, dict) and "text" in block:
                text_parts.append(block["text"])
        user_text = " ".join(text_parts)

        if self._conn is not None:
            scripted = await self._run_script(session_id, user_text)
            await self._conn.session_update(
                session_id=session_id,
                update=AgentMessageChunk(content=TextContentBlock(text=scripted or f"[echo] {user_text}")),
            )
            received = [
                f"{block.mime_type}:{len(block.data)}:{hashlib.sha256(block.data.encode()).hexdigest()[:16]}"
                for block in prompt
                if isinstance(block, ImageContentBlock)
            ]
            if received:
                await self._conn.session_update(
                    session_id=session_id,
                    update=AgentMessageChunk(content=TextContentBlock(text=f"[images] {' '.join(received)}")),
                )

        return PromptResponse(stop_reason="end_turn")

    async def _run_script(self, session_id: str, text: str) -> str | None:
        """The report of a ``/permission``, ``/write``, ``/read`` or ``/edit`` prompt, or None for any other prompt."""
        verb, _, rest = text.partition(" ")
        if verb == "/argv":
            return f"[argv] {json.dumps(sys.argv[1:])}"
        if verb not in ("/permission", "/write", "/read", "/edit"):
            return None
        args = json.loads(rest)
        try:
            if verb == "/permission":
                return await self._ask(session_id, args)
            if verb == "/write":
                return await self._write(session_id, args)
            if verb == "/edit":
                # An agent that edits asks for permission first, then writes the file it named.
                asked = await self._ask(
                    session_id, {"kind": "edit", "title": f"Edit {args['path']}", "locations": [args["path"]]}
                )
                return f"{asked} {await self._write(session_id, args)}"
            return await self._read(session_id, args)
        except Exception as error:
            # Reported instead of raised, so a test sees what broke rather than a bare JSON-RPC "Internal error".
            return f"[{verb[1:]}] crashed {type(error).__name__}: {error}"

    async def _ask(self, session_id: str, args: dict[str, Any]) -> str:
        tool_call = ToolCallUpdate(
            tool_call_id="call-1",
            kind=args.get("kind"),
            title=args.get("title"),
            raw_input=args.get("raw_input"),
            locations=[ToolCallLocation(path=path) for path in args.get("locations", [])] or None,
            content=[FileEditToolCallContent(type="diff", path=args["diff_path"], new_text="new")]
            if "diff_path" in args
            else None,
        )
        response = await self._conn.request_permission(session_id=session_id, tool_call=tool_call, options=_OPTIONS)
        return f"[permission] {response.outcome.outcome} {getattr(response.outcome, 'option_id', '')}".strip()

    async def _write(self, session_id: str, args: dict[str, Any]) -> str:
        try:
            await self._conn.write_text_file(session_id=session_id, path=args["path"], content=args["content"])
        except acp.RequestError as error:
            return _failure("write", error)
        return "[write] ok"

    async def _read(self, session_id: str, args: dict[str, Any]) -> str:
        try:
            response = await self._conn.read_text_file(
                session_id=session_id, path=args["path"], line=args.get("line"), limit=args.get("limit")
            )
        except acp.RequestError as error:
            return _failure("read", error)
        return f"[read] {json.dumps(response.content)}"

    async def fork_session(self, cwd: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def resume_session(self, cwd: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse:
        self._sessions.pop(session_id, None)
        return CloseSessionResponse()

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        pass

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass


async def main() -> None:
    agent = MockAcpAgent()
    await acp.run_agent(agent)


if __name__ == "__main__":
    asyncio.run(main())
