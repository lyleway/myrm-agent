"""E2EE middleware: an encrypted request body reaches the route as plaintext, through every layer below it.

The app mirrors the production order, E2EE first, then a BaseHTTPMiddleware layer standing in for Auth /
SessionIdle / HostAllowlist, then the text sanitizer, then the route. Sessions and ciphertext are real.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response, StreamingResponse

from app.middleware.e2ee import E2EEMiddleware
from app.middleware.max_body_size import MAX_REQUEST_BODY_BYTES, MaxBodySizeMiddleware
from app.middleware.text_sanitizer_middleware import TextSanitizerMiddleware
from app.remote_access.e2ee import (
    E2EE_CONTENT_TYPE,
    E2EE_PAIR_CIPHERTEXT_HEADER,
    E2EE_PAIR_QUERY_PARAM,
    E2EE_SESSION_HEADER,
    E2EESession,
    encrypt_utf8,
    generate_keypair,
    get_e2ee_session_store,
    public_key_b64,
)
from tests.support.asgi_exchange import AsgiExchange, run_to_completion

_CHAT_PATH = "/api/v1/chats/c1"
_STREAM_PATH = "/api/v1/agents/agent-stream"
_PLAIN = {"message": "hello", "n": 7}


class _LayerBelowE2EE(BaseHTTPMiddleware):
    """Stands in for the BaseHTTPMiddleware layers that sit between E2EE and the routes in production."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        return await call_next(request)


def _build_app(stream_gate: asyncio.Event | None = None) -> FastAPI:
    app = FastAPI()

    @app.post(_CHAT_PATH)
    async def _chat(request: Request) -> dict[str, object]:
        return {
            "body": await request.json(),
            "contentType": request.headers.get("content-type"),
            "contentLength": request.headers.get("content-length"),
            "pairToken": getattr(request.state, "e2ee_pair_token", None),
            "sessionId": getattr(request.state, "e2ee_session_id", None),
        }

    @app.get(_CHAT_PATH)
    async def _chat_get(request: Request) -> dict[str, object]:
        return {"pairToken": getattr(request.state, "e2ee_pair_token", None)}

    @app.post(_STREAM_PATH)
    async def _stream(request: Request) -> StreamingResponse:
        payload = await request.json()

        async def chunks() -> AsyncIterator[bytes]:
            yield b"data: " + json.dumps(payload).encode() + b"\n\n"
            if stream_gate is not None:
                await stream_gate.wait()
            yield b"data: done\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    app.add_middleware(TextSanitizerMiddleware)
    app.add_middleware(_LayerBelowE2EE)
    app.add_middleware(E2EEMiddleware)
    return app


class _Client:
    """A mobile client with a real E2EE session against the daemon."""

    def __init__(self, session: E2EESession, client_secret: bytes, daemon_public: bytes) -> None:
        self.session = session
        self._client_secret = client_secret
        self._daemon_public = daemon_public

    def encrypt(self, text: str) -> str:
        return encrypt_utf8(secret_key=self._client_secret, peer_public_key=self._daemon_public, text=text)

    def envelope(self, plain: dict[str, object]) -> bytes:
        return json.dumps({"v": 1, "c": self.encrypt(json.dumps(plain))}).encode()

    def headers(self, content_type: str = E2EE_CONTENT_TYPE) -> list[tuple[str, str]]:
        return [("content-type", content_type), (E2EE_SESSION_HEADER, self.session.session_id)]


@pytest_asyncio.fixture
async def client() -> _Client:
    client_public, client_secret = generate_keypair()
    daemon_public, daemon_secret = generate_keypair()
    session = await get_e2ee_session_store().create_from_hello(
        client_public_key_b64=public_key_b64(client_public),
        daemon_secret_key=daemon_secret,
    )
    return _Client(session, client_secret, daemon_public)


# Uvicorn announces 2.3; a server that implements 2.4 is the other shape a route can meet.
_SPEC_VERSIONS = pytest.mark.parametrize("spec_version", ["2.3", "2.4"])


class TestEncryptedBodyReachesTheRoute:
    @_SPEC_VERSIONS
    @pytest.mark.asyncio
    async def test_a_plain_route_reads_the_plaintext(self, client: _Client, spec_version: str) -> None:
        exchange = AsgiExchange(
            _build_app(),
            "POST",
            _CHAT_PATH,
            headers=client.headers(),
            body_chunks=(client.envelope(_PLAIN),),
            spec_version=spec_version,
        )

        status, _, body = await run_to_completion(exchange)

        assert status == 200
        assert json.loads(body)["body"] == _PLAIN

    @_SPEC_VERSIONS
    @pytest.mark.asyncio
    async def test_a_streaming_route_reads_the_plaintext_and_streams(self, client: _Client, spec_version: str) -> None:
        exchange = AsgiExchange(
            _build_app(),
            "POST",
            _STREAM_PATH,
            headers=client.headers(),
            body_chunks=(client.envelope(_PLAIN),),
            spec_version=spec_version,
        )

        status, _, body = await run_to_completion(exchange)

        assert status == 200
        assert body == b"data: " + json.dumps(_PLAIN).encode() + b"\n\ndata: done\n\n"

    @pytest.mark.asyncio
    async def test_the_envelope_may_also_arrive_as_plain_json(self, client: _Client) -> None:
        exchange = AsgiExchange(
            _build_app(),
            "POST",
            _CHAT_PATH,
            headers=client.headers("application/json"),
            body_chunks=(client.envelope(_PLAIN),),
        )

        _, _, body = await run_to_completion(exchange)

        assert json.loads(body)["body"] == _PLAIN

    @pytest.mark.asyncio
    async def test_a_body_that_arrives_in_pieces_is_decrypted_whole(self, client: _Client) -> None:
        envelope = client.envelope({"message": "x" * 5000})
        pieces = (envelope[:100], envelope[100:2000], envelope[2000:])
        exchange = AsgiExchange(_build_app(), "POST", _CHAT_PATH, headers=client.headers(), body_chunks=pieces)

        _, _, body = await run_to_completion(exchange)

        assert json.loads(body)["body"] == {"message": "x" * 5000}

    @pytest.mark.asyncio
    async def test_the_route_sees_headers_that_describe_the_plaintext(self, client: _Client) -> None:
        exchange = AsgiExchange(
            _build_app(),
            "POST",
            _CHAT_PATH,
            headers=[*client.headers(), ("content-length", "999")],
            body_chunks=(client.envelope(_PLAIN),),
        )

        _, _, body = await run_to_completion(exchange)

        seen = json.loads(body)
        assert seen["contentType"] == "application/json"
        assert seen["contentLength"] == str(len(json.dumps(_PLAIN)))

    @pytest.mark.asyncio
    async def test_the_route_knows_which_session_the_request_came_through(self, client: _Client) -> None:
        exchange = AsgiExchange(
            _build_app(), "POST", _CHAT_PATH, headers=client.headers(), body_chunks=(client.envelope(_PLAIN),)
        )

        _, _, body = await run_to_completion(exchange)

        assert json.loads(body)["sessionId"] == client.session.session_id

    @pytest.mark.asyncio
    async def test_the_decrypted_text_still_goes_through_the_sanitizer(self, client: _Client) -> None:
        exchange = AsgiExchange(
            _build_app(),
            "POST",
            _CHAT_PATH,
            headers=client.headers(),
            body_chunks=(client.envelope({"message": "a\x00b"}),),
        )

        _, _, body = await run_to_completion(exchange)

        assert json.loads(body)["body"] == {"message": "ab"}


class TestTheReceiveChannelStaysHonest:
    @pytest.mark.asyncio
    async def test_the_client_disconnect_stops_a_running_stream(self, client: _Client) -> None:
        """A phone that drops an SSE connection must stop the work behind it: the disconnect has to get through."""
        app = _build_app()
        stopped = asyncio.Event()

        @app.post("/api/v1/chats/watch-stream")
        async def _watch_stream(request: Request) -> StreamingResponse:
            await request.body()

            async def chunks() -> AsyncIterator[bytes]:
                try:
                    yield b"data: first\n\n"
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            return StreamingResponse(chunks(), media_type="text/event-stream")

        exchange = AsgiExchange(
            app,
            "POST",
            "/api/v1/chats/watch-stream",
            headers=client.headers(),
            body_chunks=(client.envelope(_PLAIN),),
            spec_version="2.3",
        )
        exchange.start()
        try:
            assert (await exchange.next_message())["type"] == "http.response.start"
            assert (await exchange.next_message())["body"] == b"data: first\n\n"
            exchange.disconnect()
            await asyncio.wait_for(stopped.wait(), timeout=5)
            await exchange.finish()
        finally:
            await exchange.cancel()

        assert exchange.app_error is None

    @pytest.mark.asyncio
    async def test_a_stream_reaches_the_client_before_the_route_is_done(self, client: _Client) -> None:
        gate = asyncio.Event()
        exchange = AsgiExchange(
            _build_app(gate), "POST", _STREAM_PATH, headers=client.headers(), body_chunks=(client.envelope(_PLAIN),)
        )
        exchange.start()
        try:
            assert (await exchange.next_message())["type"] == "http.response.start"
            first = await exchange.next_message()

            assert first["body"] == b"data: " + json.dumps(_PLAIN).encode() + b"\n\n"
            assert first["more_body"] is True
            gate.set()
            await exchange.finish()
        finally:
            await exchange.cancel()

    @pytest.mark.asyncio
    async def test_a_client_that_drops_mid_body_is_left_to_the_layer_that_answers_for_disconnects(self, client: _Client) -> None:
        app = _build_app()
        app.add_middleware(MaxBodySizeMiddleware, max_size=MAX_REQUEST_BODY_BYTES)
        exchange = AsgiExchange(
            app, "POST", _CHAT_PATH, headers=client.headers(), body_chunks=(client.envelope(_PLAIN)[:50],), truncated=True
        )

        exchange.start()
        await exchange.finish()

        assert exchange.app_error is None
        assert exchange.drain() == []


class TestWhatTheMiddlewareDoesNotTouch:
    @pytest.mark.asyncio
    async def test_a_forged_envelope_is_refused_and_never_reaches_the_route(self, client: _Client) -> None:
        envelope = json.loads(client.envelope(_PLAIN))
        envelope["c"] = envelope["c"][:-4] + "AAAA"
        exchange = AsgiExchange(
            _build_app(), "POST", _CHAT_PATH, headers=client.headers(), body_chunks=(json.dumps(envelope).encode(),)
        )

        status, _, body = await run_to_completion(exchange)

        assert status == 400
        assert json.loads(body) == {"detail": "E2EE decrypt failed"}

    @pytest.mark.asyncio
    async def test_a_body_that_is_not_an_envelope_is_refused(self, client: _Client) -> None:
        exchange = AsgiExchange(_build_app(), "POST", _CHAT_PATH, headers=client.headers(), body_chunks=(b'{"v": 1}',))

        status, _, _ = await run_to_completion(exchange)

        assert status == 400

    @pytest.mark.asyncio
    async def test_an_unknown_session_passes_through_untouched(self, client: _Client) -> None:
        body = client.envelope(_PLAIN)
        exchange = AsgiExchange(
            _build_app(),
            "POST",
            _CHAT_PATH,
            headers=[("content-type", E2EE_CONTENT_TYPE), (E2EE_SESSION_HEADER, "no-such-session")],
            body_chunks=(body,),
        )

        _, _, response = await run_to_completion(exchange)

        assert json.loads(response)["body"] == json.loads(body)

    @pytest.mark.asyncio
    async def test_a_path_that_is_not_mobile_control_is_untouched(self, client: _Client) -> None:
        app = _build_app()

        @app.post("/api/v1/unrelated")
        async def _unrelated(request: Request) -> dict[str, object]:
            return {"body": await request.json()}

        body = client.envelope(_PLAIN)
        exchange = AsgiExchange(app, "POST", "/api/v1/unrelated", headers=client.headers(), body_chunks=(body,))

        _, _, response = await run_to_completion(exchange)

        assert json.loads(response)["body"] == json.loads(body)

    @pytest.mark.asyncio
    async def test_plain_json_that_is_not_an_envelope_is_left_alone(self, client: _Client) -> None:
        exchange = AsgiExchange(
            _build_app(),
            "POST",
            _CHAT_PATH,
            headers=client.headers("application/json"),
            body_chunks=(json.dumps(_PLAIN).encode(),),
        )

        _, _, body = await run_to_completion(exchange)

        assert json.loads(body)["body"] == _PLAIN

    @pytest.mark.asyncio
    async def test_a_request_without_a_body_goes_through(self, client: _Client) -> None:
        app = _build_app()

        @app.post("/api/v1/chats/empty")
        async def _empty(request: Request) -> dict[str, object]:
            return {"length": len(await request.body())}

        exchange = AsgiExchange(app, "POST", "/api/v1/chats/empty", headers=client.headers(), body_chunks=(b"",))

        status, _, body = await run_to_completion(exchange)

        assert status == 200
        assert json.loads(body) == {"length": 0}


class TestPairToken:
    @pytest.mark.parametrize("carrier", ["header", "query"])
    @pytest.mark.asyncio
    async def test_the_pair_token_is_decrypted_for_the_layers_below(self, client: _Client, carrier: str) -> None:
        cipher = client.encrypt("pair-token-123")
        if carrier == "header":
            exchange = AsgiExchange(
                _build_app(),
                "GET",
                _CHAT_PATH,
                headers=[(E2EE_SESSION_HEADER, client.session.session_id), (E2EE_PAIR_CIPHERTEXT_HEADER, cipher)],
            )
        else:
            exchange = AsgiExchange(
                _build_app(),
                "GET",
                _CHAT_PATH,
                headers=[(E2EE_SESSION_HEADER, client.session.session_id)],
                query_string=urlencode({E2EE_PAIR_QUERY_PARAM: cipher}).encode(),
            )

        status, _, body = await run_to_completion(exchange)

        assert status == 200
        assert json.loads(body) == {"pairToken": "pair-token-123"}

    @pytest.mark.asyncio
    async def test_a_forged_pair_token_is_refused(self, client: _Client) -> None:
        exchange = AsgiExchange(
            _build_app(),
            "GET",
            _CHAT_PATH,
            headers=[(E2EE_SESSION_HEADER, client.session.session_id), (E2EE_PAIR_CIPHERTEXT_HEADER, "not-a-ciphertext")],
        )

        status, _, _ = await run_to_completion(exchange)

        assert status == 400


class TestSession:
    @pytest.mark.asyncio
    async def test_a_served_request_extends_the_session(self, client: _Client) -> None:
        store = get_e2ee_session_store()
        before = (await store.get(client.session.session_id)).expires_at  # type: ignore[union-attr]
        await asyncio.sleep(0.01)
        exchange = AsgiExchange(
            _build_app(), "POST", _CHAT_PATH, headers=client.headers(), body_chunks=(client.envelope(_PLAIN),)
        )

        await run_to_completion(exchange)

        refreshed = await store.get(client.session.session_id)
        assert refreshed is not None
        assert refreshed.expires_at > before
