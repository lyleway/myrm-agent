"""A scripted Anthropic Messages API on a loopback port, so a real ``claude`` binary can be driven without a login.

``claude`` talks to whatever ``ANTHROPIC_BASE_URL`` names. The scripted model asks for one tool call on its first
request (any request that lists the tool and carries no tool result yet) and answers with plain text once the tool's
result comes back, which is the shortest conversation that makes Claude Code ask for permission and then act on the
answer. Every request body is kept, so a test can read what the model was told about the tool call.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import TracebackType

from aiohttp import web


def _sse(event: str, data: Mapping[str, object]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


@dataclass
class FakeAnthropicApi:
    """The model: calls ``tool_name`` with ``tool_input`` once, then says ``final_text``."""

    tool_name: str
    tool_input: Mapping[str, object]
    final_text: str = "finished"
    requests: list[dict[str, object]] = field(default_factory=list)
    _runner: web.AppRunner | None = None
    _port: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def tool_results(self) -> list[dict[str, object]]:
        """The ``tool_result`` blocks Claude Code sent back to the model, in order."""
        results: list[dict[str, object]] = []
        for body in self.requests:
            messages = body.get("messages")
            last = messages[-1] if isinstance(messages, list) and messages else {}
            content = last.get("content") if isinstance(last, dict) else None
            if isinstance(content, list):
                results.extend(
                    block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"
                )
        return results

    async def __aenter__(self) -> FakeAnthropicApi:
        app = web.Application()
        app.router.add_post("/v1/messages", self._messages)
        app.router.add_route("*", "/{tail:.*}", self._anything_else)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", 0).start()
        (host_and_port,) = self._runner.addresses
        self._port = int(host_and_port[1])
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _anything_else(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _messages(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append(body)
        tools = [tool.get("name") for tool in body.get("tools", []) if isinstance(tool, dict)]
        asks_for_the_tool = self.tool_name in tools and not self.tool_results
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
        if asks_for_the_tool:
            block = {"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:20], "name": self.tool_name, "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(dict(self.tool_input))}
            stop_reason = "tool_use"
        else:
            block = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": self.final_text}
            stop_reason = "end_turn"
        await response.write(
            _sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block})
        )
        await response.write(_sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}))
        await response.write(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
        done = {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 5},
        }
        await response.write(_sse("message_delta", done))
        await response.write(_sse("message_stop", {"type": "message_stop"}))
        return response
