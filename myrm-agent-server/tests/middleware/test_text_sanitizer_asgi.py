"""TextSanitizerMiddleware as a pure ASGI layer: what it does on the receive channel and to the scope."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.middleware.max_body_size import MAX_REQUEST_BODY_BYTES, MaxBodySizeMiddleware
from app.middleware.text_sanitizer_middleware import TextSanitizerMiddleware
from tests.support.asgi_exchange import AsgiExchange, run_to_completion


class _LayerAboveTheSanitizer(BaseHTTPMiddleware):
    """A BaseHTTPMiddleware layer outside the sanitizer, as Auth and SessionIdle are in production."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        return await call_next(request)


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/echo")
    async def _echo(request: Request) -> dict[str, object]:
        return {
            "body": (await request.body()).decode("latin-1"),
            "length": request.headers.get("content-length"),
            "query": request.url.query,
        }

    @app.post("/stream")
    async def _stream(request: Request) -> StreamingResponse:
        payload = await request.json()

        async def chunks() -> AsyncIterator[bytes]:
            yield json.dumps(payload).encode()

        return StreamingResponse(chunks(), media_type="text/event-stream")

    app.add_middleware(TextSanitizerMiddleware)
    app.add_middleware(_LayerAboveTheSanitizer)
    return app


class TestBody:
    @pytest.mark.asyncio
    async def test_a_body_in_pieces_is_sanitized_whole(self) -> None:
        raw = json.dumps({"a": "x\x00y", "b": "z" * 4000}).encode()
        exchange = AsgiExchange(
            _app(),
            "POST",
            "/stream",
            headers=[("content-type", "application/json")],
            body_chunks=(raw[:10], raw[10:3000], raw[3000:]),
        )

        _, _, body = await run_to_completion(exchange)

        assert json.loads(body) == {"a": "xy", "b": "z" * 4000}

    @pytest.mark.asyncio
    async def test_the_length_the_route_sees_is_the_length_of_what_it_reads(self) -> None:
        raw = json.dumps({"a": "x\x00y"}).encode()
        exchange = AsgiExchange(
            _app(),
            "POST",
            "/echo",
            headers=[("content-type", "application/json"), ("content-length", str(len(raw)))],
            body_chunks=(raw,),
        )

        _, _, body = await run_to_completion(exchange)

        seen = json.loads(body)
        assert seen["length"] == str(len(json.dumps({"a": "xy"})))

    @pytest.mark.parametrize(
        "raw",
        [b"", b"not json {", b'"just a string"', b"42", b"[1, 2"],
        ids=["empty", "malformed", "scalar string", "scalar number", "truncated"],
    )
    @pytest.mark.asyncio
    async def test_a_body_that_is_not_a_json_container_reaches_the_route_byte_for_byte(self, raw: bytes) -> None:
        exchange = AsgiExchange(
            _app(),
            "POST",
            "/echo",
            headers=[("content-type", "application/json"), ("content-length", str(len(raw)))],
            body_chunks=(raw,),
        )

        _, _, body = await run_to_completion(exchange)

        seen = json.loads(body)
        assert seen["body"].encode("latin-1") == raw
        assert seen["length"] == str(len(raw))

    @pytest.mark.asyncio
    async def test_a_client_that_drops_mid_body_is_left_to_the_layer_that_answers_for_disconnects(self) -> None:
        raw = json.dumps({"a": "b"}).encode()
        app = _app()
        app.add_middleware(MaxBodySizeMiddleware, max_size=MAX_REQUEST_BODY_BYTES)
        exchange = AsgiExchange(
            app, "POST", "/echo", headers=[("content-type", "application/json")], body_chunks=(raw[:3],), truncated=True
        )

        exchange.start()
        await exchange.finish()

        assert exchange.app_error is None
        assert exchange.drain() == []

    @pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
    @pytest.mark.asyncio
    async def test_a_streaming_route_below_a_base_http_layer_still_streams(self, spec_version: str) -> None:
        raw = json.dumps({"a": "b"}).encode()
        exchange = AsgiExchange(
            _app(),
            "POST",
            "/stream",
            headers=[("content-type", "application/json")],
            body_chunks=(raw,),
            spec_version=spec_version,
        )

        status, _, body = await run_to_completion(exchange)

        assert status == 200
        assert json.loads(body) == {"a": "b"}


class TestScope:
    @pytest.mark.asyncio
    async def test_the_query_string_is_sanitized_for_the_route_only(self) -> None:
        seen_by_outer_layer: list[bytes] = []

        class _Probe:
            def __init__(self, app: ASGIApp) -> None:
                self.app = app

            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                await self.app(scope, receive, send)
                seen_by_outer_layer.append(scope["query_string"])

        app = _app()
        app.add_middleware(_Probe)
        exchange = AsgiExchange(
            app,
            "POST",
            "/echo",
            headers=[("content-type", "text/plain")],
            body_chunks=(b"x",),
            query_string=b"q=a%00b",
        )

        _, _, body = await run_to_completion(exchange)

        assert json.loads(body)["query"] == "q=ab"
        assert seen_by_outer_layer == [b"q=a%00b"], "the caller's scope must come back as it went in"

    @pytest.mark.asyncio
    async def test_a_websocket_connection_passes_through_untouched(self) -> None:
        reached: list[Scope] = []

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            reached.append(scope)

        scope: Scope = {"type": "websocket", "path": "/ws", "query_string": b"q=a%00b", "headers": []}

        async def receive() -> Message:
            raise AssertionError("a websocket is not the sanitizer's to read")

        async def send(message: Message) -> None:
            raise AssertionError(f"nothing may be sent: {message}")

        await TextSanitizerMiddleware(downstream)(scope, receive, send)

        assert reached == [scope]
        assert reached[0]["query_string"] == b"q=a%00b"
