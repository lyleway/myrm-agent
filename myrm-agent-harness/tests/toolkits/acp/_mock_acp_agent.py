"""Minimal ACP agent for end-to-end testing.

Run as a subprocess: python tests/toolkits/acp/_mock_acp_agent.py
Implements the acp.Agent protocol over stdin/stdout, echoing prompts back
with a prefix to verify the full ACP roundtrip. With ``--images`` it advertises
``promptCapabilities.image`` and reports every image block it received as
``[images] <mimeType>:<base64 length>:<sha256 prefix>``.

A prompt that starts with ``/permission``, ``/write`` or ``/edit`` followed by a JSON object makes the agent call
the client the way a real agent does, over the real SDK, and report what came back:
``/permission {"kind", "title", "raw_input", "locations", "diff_path"}`` -> ``[permission] <outcome> <option id>``;
``/write {"path", "content"}`` -> ``[write] ok`` or ``[write] error <code> <message> <data as JSON>``;
``/edit {"path", "content"}`` asks permission to edit the file, then writes it: both reports, joined.
``/argv`` -> ``[argv] <the arguments the agent was started with, as JSON>``;
``/mode`` -> ``[mode] <the session's current mode>``;
``/capabilities`` -> ``[capabilities] <the capabilities the client offered in initialize, as JSON>``;
``/session`` -> ``[session] <the ACP session id the prompt runs in>``;
``/history`` -> ``[history] <the earlier prompts of the session, joined by "|">``;
``/story`` says "before", runs a tool call and then says "after", as an agent that speaks around a tool call does;
``/stop <reason>`` ends the prompt with that ACP stop reason.
A ``session/cancel`` makes the prompt that is running end with stop reason ``cancelled`` once what it waits for is
answered, as the real agents do.

Session modes: ``--mode <id>`` makes ``session/new`` report that current mode (offered with ``--modes a,b``, default
``default,yolo``), ``set_session_mode`` switches it unless the agent was started with ``--keep-mode``. ``--login-required``
makes ``session/new`` answer the way an agent without a login does (JSON-RPC ``auth_required``) and ``--bad-session``
with an unrelated JSON-RPC error.

Session continuity: ``--state-file <path>`` keeps the sessions and their prompts on disk, so a restarted agent still knows
them. ``--resume`` advertises ``sessionCapabilities.resume`` and ``--load`` advertises ``loadSession``; ``session/load``
replays the stored prompts as updates first, ``session/resume`` does not. A session the agent does not know is answered
with JSON-RPC ``resource_not_found``; ``--continue-needs-login`` answers both with ``auth_required`` instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any, cast

import acp
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    ClientCapabilities,
    CloseSessionResponse,
    FileEditToolCallContent,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PermissionOption,
    PromptCapabilities,
    PromptResponse,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionMode,
    SessionModeState,
    SessionResumeCapabilities,
    StopReason,
    TextContentBlock,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UserMessageChunk,
)

_OPTIONS = [
    PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
    PermissionOption(option_id="allow-always", name="Allow always", kind="allow_always"),
    PermissionOption(option_id="reject-once", name="Reject once", kind="reject_once"),
    PermissionOption(option_id="reject-always", name="Reject always", kind="reject_always"),
]


def _flag_value(name: str) -> str | None:
    """The value after ``--name`` on the agent's own command line, or None when the flag is absent."""
    args = sys.argv[1:]
    return args[args.index(name) + 1] if name in args else None


def _failure(verb: str, error: acp.RequestError) -> str:
    """How a JSON-RPC error from the client reads in a report: the code, the message and the error's data."""
    return f"[{verb}] error {error.code} {error} {json.dumps(error.data)}"


class MockAcpAgent:
    """Echo agent that mirrors prompts back through ACP protocol."""

    def __init__(self) -> None:
        self._conn: acp.Client | None = None
        self._sessions: dict[str, str] = {}
        self._history: dict[str, list[str]] = {}
        self._modes: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._stop_reason: StopReason | None = None
        self._client_capabilities: ClientCapabilities | None = None
        self._state_path = _flag_value("--state-file")
        self._restore()

    def _restore(self) -> None:
        """Take back the sessions a previous run of this agent kept in its state file."""
        if self._state_path is None or not Path(self._state_path).exists():
            return
        stored = json.loads(Path(self._state_path).read_text())
        self._sessions = {session_id: entry["cwd"] for session_id, entry in stored.items()}
        self._history = {session_id: entry["history"] for session_id, entry in stored.items()}

    def _persist(self) -> None:
        if self._state_path is None:
            return
        stored = {
            session_id: {"cwd": cwd, "history": self._history.get(session_id, [])}
            for session_id, cwd in self._sessions.items()
        }
        Path(self._state_path).write_text(json.dumps(stored))

    def _require_known(self, session_id: str) -> None:
        """Answer the way an agent does for a session it does not know, or one it cannot open without a login."""
        if "--continue-needs-login" in sys.argv[1:]:
            raise acp.RequestError.auth_required()
        if session_id not in self._sessions:
            raise acp.RequestError.resource_not_found(session_id)

    def on_connect(self, conn: acp.Client) -> None:
        self._conn = conn

    @property
    def _client(self) -> acp.Client:
        assert self._conn is not None, "the agent is not connected to a client"
        return self._conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: object | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        advertise_images = "--images" in sys.argv[1:]
        self._client_capabilities = client_capabilities
        sessions = SessionCapabilities(resume=SessionResumeCapabilities()) if "--resume" in sys.argv[1:] else None
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(
                load_session="--load" in sys.argv[1:],
                prompt_capabilities=PromptCapabilities(image=advertise_images),
                session_capabilities=sessions,
            ),
            agent_info=Implementation(name="mock-acp-agent", version="1.0.0"),
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[object] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        if "--login-required" in sys.argv[1:]:
            raise acp.RequestError.auth_required()
        if "--bad-session" in sys.argv[1:]:
            raise acp.RequestError.invalid_params({"why": "scripted"})
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = cwd
        self._history[session_id] = []
        self._persist()
        mode = _flag_value("--mode")
        if mode is None:
            return NewSessionResponse(session_id=session_id)
        self._modes[session_id] = mode
        offered = (_flag_value("--modes") or "default,yolo").split(",")
        modes = SessionModeState(
            current_mode_id=mode, available_modes=[SessionMode(id=offer, name=offer) for offer in offered]
        )
        return NewSessionResponse(session_id=session_id, modes=modes)

    async def load_session(self, cwd: str, session_id: str, **kwargs: Any) -> LoadSessionResponse:
        self._require_known(session_id)
        if self._conn is not None:
            # An agent that continues a stored session replays it to the client before it answers.
            for earlier in self._history[session_id]:
                await self._conn.session_update(
                    session_id=session_id, update=UserMessageChunk(content=TextContentBlock(text=earlier))
                )
                await self._conn.session_update(
                    session_id=session_id, update=AgentMessageChunk(content=TextContentBlock(text=f"[echo] {earlier}"))
                )
        return LoadSessionResponse()

    async def list_sessions(self, **kwargs: Any) -> ListSessionsResponse:
        return ListSessionsResponse(sessions=[])

    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs: Any) -> None:
        if "--keep-mode" in sys.argv[1:]:
            raise acp.RequestError.invalid_params({"mode": mode_id})
        self._modes[session_id] = mode_id

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
        self._cancelled.discard(session_id)
        self._history.setdefault(session_id, []).append(user_text)
        self._persist()

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

        stop_reason: StopReason = self._stop_reason or "end_turn"
        self._stop_reason = None
        return PromptResponse(stop_reason="cancelled" if session_id in self._cancelled else stop_reason)

    async def _run_script(self, session_id: str, text: str) -> str | None:
        """The report of a ``/permission``, ``/write``, ``/edit`` or introspection prompt, or None for any other prompt."""
        verb, _, rest = text.partition(" ")
        if verb == "/argv":
            return f"[argv] {json.dumps(sys.argv[1:])}"
        if verb == "/mode":
            return f"[mode] {self._modes.get(session_id)}"
        if verb == "/capabilities":
            offered = self._client_capabilities
            dumped = None if offered is None else offered.model_dump(exclude_none=True)
            return f"[capabilities] {json.dumps(dumped, sort_keys=True)}"
        if verb == "/session":
            return f"[session] {session_id}"
        if verb == "/history":
            return f"[history] {'|'.join(self._history[session_id][:-1])}"
        if verb == "/story":
            return await self._tell_around_a_tool_call(session_id)
        if verb == "/stop":
            self._stop_reason = cast(StopReason, rest)
            return f"[stop] {rest}"
        if verb not in ("/permission", "/write", "/edit"):
            return None
        args = json.loads(rest)
        try:
            if verb == "/permission":
                return await self._ask(session_id, args)
            if verb == "/write":
                return await self._write(session_id, args)
            # ``/edit``: an agent that edits asks for permission first, then writes the file it named.
            asked = await self._ask(
                session_id, {"kind": "edit", "title": f"Edit {args['path']}", "locations": [args["path"]]}
            )
            return f"{asked} {await self._write(session_id, args)}"
        except Exception as error:
            # Reported instead of raised, so a test sees what broke rather than a bare JSON-RPC "Internal error".
            return f"[{verb[1:]}] crashed {type(error).__name__}: {error}"

    async def _tell_around_a_tool_call(self, session_id: str) -> str:
        """Say "before", run a tool call, and leave "after" to be said as the prompt's reply."""
        await self._client.session_update(
            session_id=session_id, update=AgentMessageChunk(content=TextContentBlock(text="before"))
        )
        await self._client.session_update(
            session_id=session_id, update=ToolCallStart(tool_call_id="story-1", title="Read", kind="read")
        )
        await self._client.session_update(
            session_id=session_id, update=ToolCallProgress(tool_call_id="story-1", status="completed")
        )
        return "after"

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
        response = await self._client.request_permission(session_id=session_id, tool_call=tool_call, options=_OPTIONS)
        return f"[permission] {response.outcome.outcome} {getattr(response.outcome, 'option_id', '')}".strip()

    async def _write(self, session_id: str, args: dict[str, Any]) -> str:
        try:
            await self._client.write_text_file(session_id=session_id, path=args["path"], content=args["content"])
        except acp.RequestError as error:
            return _failure("write", error)
        return "[write] ok"

    async def fork_session(self, cwd: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def resume_session(self, cwd: str, session_id: str, **kwargs: Any) -> ResumeSessionResponse:
        self._require_known(session_id)
        return ResumeSessionResponse()

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse:
        self._sessions.pop(session_id, None)
        return CloseSessionResponse()

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        self._cancelled.add(session_id)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass


async def main() -> None:
    agent = MockAcpAgent()
    await acp.run_agent(agent)


if __name__ == "__main__":
    asyncio.run(main())
