"""A scripted Anthropic Messages API on a loopback port, so a real ``claude`` binary can be driven without a login.

``claude`` talks to whatever ``ANTHROPIC_BASE_URL`` names. ``ScriptedAnthropicApi`` answers every request from a function
of its body, which plays a model through a conversation of any shape: a sub-agent, for one, asks the model on its own
account, in requests of its own. ``FakeAnthropicApi`` is the shortest conversation that makes Claude Code ask for
permission and then act on the answer: the model asks for one tool call on its first request (any request that lists
the tool and carries no tool result yet), saying ``lead_text`` first when it has some, and answers with plain text once
the tool's result comes back. A model told to call a tool that is not on offer just answers. Every request body is
kept, so a test can read what the model was told about the tool call.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from aiohttp import web

from tests.toolkits.acp._loopback_api import LoopbackApi


def _sse(event: str, data: Mapping[str, object]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


@dataclass(frozen=True)
class Say:
    """The model writes ``text``."""

    text: str


@dataclass(frozen=True)
class Call:
    """The model asks for the tool ``name`` with ``input``."""

    name: str
    input: Mapping[str, object]


@dataclass(frozen=True)
class Reply:
    """One message of the model: what it says and which tools it calls, in order."""

    blocks: tuple[Say | Call, ...]
    # Why the message ended, as the API reports it (``end_turn``, ``max_tokens``, ``refusal``); a message that calls
    # a tool ends with ``tool_use``, any other with ``end_turn``.
    stop_reason: str = ""

    @property
    def reported_stop_reason(self) -> str:
        if self.stop_reason:
            return self.stop_reason
        return "tool_use" if any(isinstance(block, Call) for block in self.blocks) else "end_turn"


def tool_names(body: Mapping[str, object]) -> list[str]:
    """The names of the tools a request offers the model."""
    tools = body.get("tools")
    names = [tool.get("name") for tool in tools if isinstance(tool, dict)] if isinstance(tools, list) else []
    return [name for name in names if isinstance(name, str)]


def tool_results_in(body: Mapping[str, object]) -> list[dict[str, object]]:
    """The ``tool_result`` blocks in the last user message of a request: what the tools it ran answered.

    Claude Code may add notices of its own (role ``system``) after that message, so the last message is not it.
    """
    messages = body.get("messages")
    users = (
        [m for m in messages if isinstance(m, dict) and m.get("role") == "user"] if isinstance(messages, list) else []
    )
    content = users[-1].get("content") if users else None
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]


def first_user_text(body: Mapping[str, object]) -> str:
    """The text of the first user message of a request: what started the conversation it belongs to."""
    messages = body.get("messages")
    first = messages[0] if isinstance(messages, list) and messages else {}
    content = first.get("content") if isinstance(first, dict) else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str))


@dataclass
class AnthropicApi(LoopbackApi):
    """The wire format: a subclass says what the model replies to each request."""

    def _reply(self, body: Mapping[str, object]) -> Reply:
        raise NotImplementedError

    def _register(self, app: web.Application) -> None:
        app.router.add_post("/v1/messages", self._messages)

    async def _messages(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append(body)
        reply = self._reply(body)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await response.prepare(request)
        message = {
            "id": "msg_" + uuid.uuid4().hex[:20],
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "claude-fake"),
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }
        await response.write(_sse("message_start", {"type": "message_start", "message": message}))
        for index, block in enumerate(reply.blocks):
            if isinstance(block, Say):
                started: dict[str, object] = {"type": "text", "text": ""}
                delta: dict[str, object] = {"type": "text_delta", "text": block.text}
            else:
                started = {"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:20], "name": block.name, "input": {}}
                delta = {"type": "input_json_delta", "partial_json": json.dumps(dict(block.input))}
            start = {"type": "content_block_start", "index": index, "content_block": started}
            await response.write(_sse("content_block_start", start))
            await response.write(
                _sse("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
            )
            await response.write(_sse("content_block_stop", {"type": "content_block_stop", "index": index}))
        done = {
            "type": "message_delta",
            "delta": {"stop_reason": reply.reported_stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 5},
        }
        await response.write(_sse("message_delta", done))
        await response.write(_sse("message_stop", {"type": "message_stop"}))
        return response


@dataclass
class ScriptedAnthropicApi(AnthropicApi):
    """The model: ``script`` replies to each request from its body (every request of every agent the CLI runs)."""

    script: Callable[[Mapping[str, object]], Reply]

    def _reply(self, body: Mapping[str, object]) -> Reply:
        return self.script(body)


@dataclass
class FakeAnthropicApi(AnthropicApi):
    """The model: calls ``tool_name`` with ``tool_input`` once, then says ``final_text``."""

    tool_name: str
    tool_input: Mapping[str, object]
    final_text: str = "finished"
    # What the model says in the message that asks for the tool, before it asks.
    lead_text: str = ""
    # Why the message with ``final_text`` ended, as the API reports it (``end_turn``, ``max_tokens``, ``refusal``).
    final_stop_reason: str = "end_turn"

    @property
    def tool_results(self) -> list[dict[str, object]]:
        """The ``tool_result`` blocks Claude Code sent back to the model, in order."""
        return [block for body in self.requests for block in tool_results_in(body)]

    def _reply(self, body: Mapping[str, object]) -> Reply:
        if self.tool_name in tool_names(body) and not self.tool_results:
            lead = (Say(self.lead_text),) if self.lead_text else ()
            return Reply((*lead, Call(self.tool_name, self.tool_input)))
        return Reply((Say(self.final_text),), self.final_stop_reason)
