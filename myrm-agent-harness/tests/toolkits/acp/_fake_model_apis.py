"""Scripted model APIs on a loopback port, so a real Gemini, Qwen or Codex binary can be driven without a login or a model.

``qwen`` talks to the OpenAI-compatible endpoint ``OPENAI_BASE_URL`` names, ``gemini`` to the one
``GOOGLE_GEMINI_BASE_URL`` names and ``codex`` to the Responses endpoint of the provider its config names. The scripted
model asks for its tool calls one after another, each as soon as the
result of the one before it is back and only if the agent offers a tool of that name, and then says ``final_text``:
the shortest conversation that makes an agent ask for permission and then act on the answer. Every request body is
kept, so a test can read what the model was told about each call.

An agent also sends requests of its own after a turn (memory notes, suggestions); a request whose last message is not
the prompt or the result of the call before is answered with plain text, so those never run the script a second time.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from aiohttp import web

from tests.toolkits.acp._loopback_api import LoopbackApi


@dataclass(frozen=True)
class ToolCall:
    """One tool call the scripted model asks for."""

    name: str
    arguments: Mapping[str, object]
    # The namespace a Responses API tool lives in (Codex puts the tools of an MCP server in one).
    namespace: str = ""


@dataclass
class ScriptedModelApi(LoopbackApi):
    """The model: asks for ``calls`` in order, then says ``final_text``."""

    calls: Sequence[ToolCall] = ()
    final_text: str = "finished"

    @property
    def tool_results(self) -> list[str]:
        """The results the agent sent back to the model, in order, as text (the longest conversation seen)."""
        longest: list[str] = []
        for body in self.requests:
            results = self._results_in(body)
            if len(results) > len(longest):
                longest = results
        return longest

    def _results_in(self, body: dict[str, object]) -> list[str]:
        raise NotImplementedError

    def _offered_tools(self, body: dict[str, object]) -> set[str]:
        raise NotImplementedError

    def _last_is_a_result(self, body: dict[str, object]) -> bool:
        raise NotImplementedError

    def _next_call(self, body: dict[str, object]) -> ToolCall | None:
        """The call the model makes in answer to ``body``, or None when it answers in words."""
        asked = len(self._results_in(body))
        if asked >= len(self.calls) or self._last_is_a_result(body) != (asked > 0):
            return None
        call = self.calls[asked]
        return call if call.name in self._offered_tools(body) else None


def _as_dicts(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text_of(content: object) -> str:
    """A message's content as text: a string as it is, text parts joined."""
    if isinstance(content, str):
        return content
    return "".join(str(part.get("text", "")) for part in _as_dicts(content))


@dataclass
class FakeOpenAiChatApi(ScriptedModelApi):
    """The OpenAI chat-completions wire format (``stream: true`` as SSE chunks, ``[DONE]`` at the end)."""

    def _register(self, app: web.Application) -> None:
        app.router.add_post("/v1/chat/completions", self._completions)

    def _messages(self, body: dict[str, object]) -> list[dict[str, object]]:
        return _as_dicts(body.get("messages"))

    def _results_in(self, body: dict[str, object]) -> list[str]:
        return [_text_of(message.get("content")) for message in self._messages(body) if message.get("role") == "tool"]

    def _offered_tools(self, body: dict[str, object]) -> set[str]:
        functions = (tool.get("function") for tool in _as_dicts(body.get("tools")))
        return {str(function["name"]) for function in functions if isinstance(function, dict) and "name" in function}

    def _last_is_a_result(self, body: dict[str, object]) -> bool:
        messages = self._messages(body)
        return bool(messages) and messages[-1].get("role") == "tool"

    @staticmethod
    def _chunk(delta: Mapping[str, object], finish_reason: str | None = None) -> bytes:
        chunk = {
            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fake-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(chunk)}\n\n".encode()

    async def _completions(self, request: web.Request) -> web.StreamResponse:
        body: dict[str, object] = await request.json()
        self.requests.append(body)
        if not body.get("stream"):
            message = {"role": "assistant", "content": "ok"}
            return web.json_response(
                {
                    "id": "chatcmpl-" + uuid.uuid4().hex[:12],
                    "object": "chat.completion",
                    "created": 1,
                    "model": "fake-model",
                    "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
        call = self._next_call(body)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await response.prepare(request)
        if call is None:
            await response.write(self._chunk({"role": "assistant", "content": self.final_text}))
            await response.write(self._chunk({}, "stop"))
        else:
            function = {"name": call.name, "arguments": json.dumps(dict(call.arguments))}
            tool_call = {"index": 0, "id": "call_" + uuid.uuid4().hex[:12], "type": "function", "function": function}
            await response.write(self._chunk({"role": "assistant", "content": None, "tool_calls": [tool_call]}))
            await response.write(self._chunk({}, "tool_calls"))
        await response.write(b"data: [DONE]\n\n")
        return response


@dataclass
class FakeGeminiApi(ScriptedModelApi):
    """The Gemini ``streamGenerateContent`` wire format (SSE, one ``GenerateContentResponse`` per event)."""

    def _register(self, app: web.Application) -> None:
        app.router.add_post("/v1beta/models/{model}:streamGenerateContent", self._stream)
        app.router.add_post("/v1beta/models/{model}:generateContent", self._generate)
        app.router.add_post("/v1beta/models/{model}:countTokens", self._count_tokens)

    def _contents(self, body: dict[str, object]) -> list[dict[str, object]]:
        return _as_dicts(body.get("contents"))

    def _function_responses(self, content: dict[str, object]) -> list[dict[str, object]]:
        responses = (part.get("functionResponse") for part in _as_dicts(content.get("parts")))
        return [response for response in responses if isinstance(response, dict)]

    def _results_in(self, body: dict[str, object]) -> list[str]:
        return [
            json.dumps(response.get("response"))
            for content in self._contents(body)
            for response in self._function_responses(content)
        ]

    def _offered_tools(self, body: dict[str, object]) -> set[str]:
        return {
            str(declaration["name"])
            for tool in _as_dicts(body.get("tools"))
            for declaration in _as_dicts(tool.get("functionDeclarations"))
            if "name" in declaration
        }

    def _last_is_a_result(self, body: dict[str, object]) -> bool:
        contents = self._contents(body)
        return bool(contents) and bool(self._function_responses(contents[-1]))

    @staticmethod
    def _response(parts: list[dict[str, object]]) -> dict[str, object]:
        return {
            "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        }

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        body: dict[str, object] = await request.json()
        self.requests.append(body)
        call = self._next_call(body)
        parts: list[dict[str, object]] = (
            [{"text": self.final_text}]
            if call is None
            else [{"functionCall": {"name": call.name, "args": dict(call.arguments)}}]
        )
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await response.prepare(request)
        await response.write(f"data: {json.dumps(self._response(parts))}\r\n\r\n".encode())
        return response

    async def _generate(self, request: web.Request) -> web.Response:
        self.requests.append(await request.json())
        return web.json_response(self._response([{"text": "ok"}]))

    async def _count_tokens(self, request: web.Request) -> web.Response:
        self.requests.append(await request.json())
        return web.json_response({"totalTokens": 1})


_RESULT_TYPES = ("function_call_output", "custom_tool_call_output")


@dataclass
class FakeResponsesApi(ScriptedModelApi):
    """The OpenAI Responses wire format (``POST /v1/responses`` answered as typed SSE events), which Codex speaks.

    ``fail_status`` answers every request with that HTTP status and an OpenAI-style error body; ``hang`` opens the
    stream and says nothing until ``release`` is called (a model that is slow, for a turn that is stopped meanwhile).
    A tool the request offers as ``custom`` (Codex's ``apply_patch``) is called with ``arguments["input"]`` as its text.
    """

    fail_status: int | None = None
    hang: bool = False
    input_tokens: int = 10
    output_tokens: int = 5
    authorizations: list[str] = field(default_factory=list, init=False)
    _released: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)

    def release(self) -> None:
        self._released.set()

    def _register(self, app: web.Application) -> None:
        app.router.add_post("/v1/responses", self._responses)

    def _input(self, body: dict[str, object]) -> list[dict[str, object]]:
        return _as_dicts(body.get("input"))

    def _results_in(self, body: dict[str, object]) -> list[str]:
        return [str(item.get("output")) for item in self._input(body) if item.get("type") in _RESULT_TYPES]

    def _offered_tools(self, body: dict[str, object]) -> set[str]:
        tools = _as_dicts(body.get("tools"))
        nested = (child for tool in tools for child in _as_dicts(tool.get("tools")))
        return {str(tool["name"]) for tool in (*tools, *nested) if "name" in tool}

    def _last_is_a_result(self, body: dict[str, object]) -> bool:
        items = self._input(body)
        return bool(items) and items[-1].get("type") in _RESULT_TYPES

    def _tool_item(self, call: ToolCall, body: dict[str, object], call_id: str) -> dict[str, object]:
        custom = any(
            tool.get("type") == "custom" and tool.get("name") == call.name for tool in _as_dicts(body.get("tools"))
        )
        if custom:
            return {
                "type": "custom_tool_call",
                "call_id": call_id,
                "name": call.name,
                "input": str(call.arguments.get("input", "")),
            }
        item: dict[str, object] = {
            "type": "function_call",
            "call_id": call_id,
            "name": call.name,
            "arguments": json.dumps(dict(call.arguments)),
        }
        if call.namespace:
            item["namespace"] = call.namespace
        return item

    def _usage(self) -> dict[str, object]:
        return {
            "input_tokens": self.input_tokens,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": self.output_tokens,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": self.input_tokens + self.output_tokens,
        }

    @staticmethod
    def _sse(events: Sequence[Mapping[str, object]]) -> bytes:
        return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()

    async def _responses(self, request: web.Request) -> web.StreamResponse:
        body: dict[str, object] = await request.json()
        self.requests.append(body)
        self.authorizations.append(request.headers.get("Authorization", ""))
        if self.fail_status is not None:
            error = {"message": f"stub failure {self.fail_status}", "type": "stub", "code": "stub"}
            return web.json_response({"error": error}, status=self.fail_status)
        response_id = f"resp_{len(self.requests)}"
        events: list[Mapping[str, object]] = [{"type": "response.created", "response": {"id": response_id}}]
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        if self.hang:
            await response.write(self._sse(events))
            await self._released.wait()
            return response
        call = self._next_call(body)
        if call is not None:
            events.append(
                {
                    "type": "response.output_item.done",
                    "item": self._tool_item(call, body, f"call_{len(self._results_in(body))}"),
                }
            )
        else:
            message: dict[str, object] = {
                "type": "message",
                "role": "assistant",
                "id": f"msg_{len(self.requests)}",
                "content": [{"type": "output_text", "text": self.final_text}],
            }
            half = len(self.final_text) // 2
            events.append({"type": "response.output_item.added", "item": {**message, "content": []}})
            for delta in (self.final_text[:half], self.final_text[half:]):
                events.append(
                    {
                        "type": "response.output_text.delta",
                        "item_id": message["id"],
                        "output_index": 0,
                        "content_index": 0,
                        "delta": delta,
                    }
                )
            events.append({"type": "response.output_item.done", "item": message})
        events.append({"type": "response.completed", "response": {"id": response_id, "usage": self._usage()}})
        await response.write(self._sse(events))
        return response
