"""Tests for the pure-ASGI request body primitives."""

from __future__ import annotations

import pytest
from starlette.requests import ClientDisconnect
from starlette.types import Message, Receive, Scope

from app.middleware.asgi_body import describe_replaced_body, read_request_body, replay_request_body


def _receive_of(*messages: Message) -> tuple[Receive, list[Message]]:
    queue = list(messages)

    async def receive() -> Message:
        return queue.pop(0)

    return receive, queue


class TestReadRequestBody:
    @pytest.mark.asyncio
    async def test_a_single_message_body(self) -> None:
        receive, _ = _receive_of({"type": "http.request", "body": b"abc", "more_body": False})

        assert await read_request_body(receive) == b"abc"

    @pytest.mark.asyncio
    async def test_a_body_in_pieces_is_joined_in_order(self) -> None:
        receive, queue = _receive_of(
            {"type": "http.request", "body": b"a", "more_body": True},
            {"type": "http.request", "body": b"b", "more_body": True},
            {"type": "http.request", "body": b"c"},
            {"type": "http.disconnect"},
        )

        assert await read_request_body(receive) == b"abc"
        assert queue == [{"type": "http.disconnect"}], "reading must stop at the end of the body"

    @pytest.mark.asyncio
    async def test_an_empty_body(self) -> None:
        receive, _ = _receive_of({"type": "http.request"})

        assert await read_request_body(receive) == b""

    @pytest.mark.asyncio
    async def test_a_client_that_leaves_mid_body_raises(self) -> None:
        receive, _ = _receive_of({"type": "http.request", "body": b"a", "more_body": True}, {"type": "http.disconnect"})

        with pytest.raises(ClientDisconnect):
            await read_request_body(receive)


class TestReplayRequestBody:
    @pytest.mark.asyncio
    async def test_the_replacement_is_served_once_as_the_whole_body(self) -> None:
        receive, _ = _receive_of({"type": "http.disconnect"})

        replayed = replay_request_body(b"plain", receive)

        assert await replayed() == {"type": "http.request", "body": b"plain", "more_body": False}

    @pytest.mark.asyncio
    async def test_after_the_body_the_real_receive_speaks(self) -> None:
        receive, queue = _receive_of({"type": "http.disconnect"})
        replayed = replay_request_body(b"plain", receive)

        await replayed()
        assert queue == [{"type": "http.disconnect"}], "serving the body must not consume the real receive"

        assert await replayed() == {"type": "http.disconnect"}
        assert queue == []


class TestDescribeReplacedBody:
    @staticmethod
    def _scope() -> Scope:
        return {
            "type": "http",
            "headers": [
                (b"host", b"testserver"),
                (b"Content-Length", b"999"),
                (b"transfer-encoding", b"chunked"),
                (b"content-type", b"application/e2ee+json"),
                (b"x-request-id", b"r1"),
            ],
            "state": {"seen": True},
        }

    def test_the_length_describes_the_replacement_and_chunking_is_dropped(self) -> None:
        described = describe_replaced_body(self._scope(), b"12345")

        headers = dict(described["headers"])
        assert headers[b"content-length"] == b"5"
        assert b"Content-Length" not in headers
        assert b"transfer-encoding" not in headers

    def test_the_content_type_is_kept_unless_the_rewrite_changes_it(self) -> None:
        kept = dict(describe_replaced_body(self._scope(), b"x")["headers"])
        replaced = dict(describe_replaced_body(self._scope(), b"x", content_type="application/json")["headers"])

        assert kept[b"content-type"] == b"application/e2ee+json"
        assert replaced[b"content-type"] == b"application/json"

    def test_every_other_header_survives(self) -> None:
        described = describe_replaced_body(self._scope(), b"x", content_type="application/json")

        headers = dict(described["headers"])
        assert headers[b"host"] == b"testserver"
        assert headers[b"x-request-id"] == b"r1"
        assert len(described["headers"]) == 4

    def test_the_callers_scope_is_not_modified_and_state_stays_shared(self) -> None:
        scope = self._scope()
        original_headers = list(scope["headers"])

        described = describe_replaced_body(scope, b"x", content_type="application/json")

        assert scope["headers"] == original_headers
        assert described["state"] is scope["state"]
