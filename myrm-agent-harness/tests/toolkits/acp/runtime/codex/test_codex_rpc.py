"""The JSON-RPC peer of Codex's app server: requests and their answers, notifications in order, the server's own requests."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping

import pytest

from myrm_agent_harness.toolkits.acp.runtime.codex.rpc import (
    JsonRpcPeer,
    Notification,
    RpcClosedError,
    RpcError,
    RpcTimeoutError,
)


class FakeProcess:
    """The two ends of a process's stdio that the peer uses: ``send`` to its stdin, ``lines`` from its stdout."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.send_error: OSError | None = None
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self._wrote = asyncio.Event()

    async def send(self, line: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(json.loads(line))
        self._wrote.set()

    async def lines(self) -> AsyncIterator[str]:
        while (line := await self._incoming.get()) is not None:
            yield line

    def say(self, message: Mapping[str, object] | str) -> None:
        self._incoming.put_nowait(message if isinstance(message, str) else json.dumps(message))

    def hang_up(self) -> None:
        self._incoming.put_nowait(None)

    async def written(self, count: int) -> None:
        async with asyncio.timeout(5):
            while len(self.sent) < count:
                self._wrote.clear()
                await self._wrote.wait()


async def _no_answer(method: str, params: Mapping[str, object]) -> Mapping[str, object]:
    return {}


class _Watcher:
    def __init__(self) -> None:
        self.seen: list[Notification] = []
        self.failing = False

    def __call__(self, notification: Notification) -> None:
        self.seen.append(notification)
        if self.failing:
            raise RuntimeError("the observer broke")


@pytest.fixture
def process() -> FakeProcess:
    return FakeProcess()


@pytest.fixture
def watcher() -> _Watcher:
    return _Watcher()


@pytest.fixture
async def peer(process: FakeProcess, watcher: _Watcher) -> AsyncIterator[JsonRpcPeer]:
    connection = JsonRpcPeer(process, on_server_request=_no_answer, observe=watcher)  # type: ignore[arg-type]
    yield connection
    await connection.aclose()


# -- requests ----------------------------------------------------------------------------------------------------------------


async def test_answers_find_the_request_they_belong_to_whatever_the_order(
    process: FakeProcess, peer: JsonRpcPeer
) -> None:
    first = asyncio.create_task(peer.request("first", {"x": 1}, timeout=5))
    second = asyncio.create_task(peer.request("second", timeout=5))
    await process.written(2)

    process.say({"id": 2, "result": {"which": "second"}})
    process.say({"id": 1, "result": {"which": "first"}})

    assert await first == {"which": "first"}
    assert await second == {"which": "second"}
    assert process.sent == [
        {"id": 1, "method": "first", "params": {"x": 1}},
        {"id": 2, "method": "second", "params": {}},
    ]


async def test_an_error_answer_raises_with_the_servers_code_and_words(process: FakeProcess, peer: JsonRpcPeer) -> None:
    pending = asyncio.create_task(peer.request("m", timeout=5))
    await process.written(1)

    process.say({"id": 1, "error": {"code": -32600, "message": "no rollout found"}})

    with pytest.raises(RpcError) as raised:
        await pending
    assert (raised.value.code, raised.value.message) == (-32600, "no rollout found")


async def test_an_error_without_a_numeric_code_is_an_internal_error(process: FakeProcess, peer: JsonRpcPeer) -> None:
    pending = asyncio.create_task(peer.request("m", timeout=5))
    await process.written(1)

    process.say({"id": 1, "error": {"message": "odd"}})

    with pytest.raises(RpcError) as raised:
        await pending
    assert raised.value.code == -32603


async def test_a_result_that_is_not_an_object_is_an_empty_one(process: FakeProcess, peer: JsonRpcPeer) -> None:
    pending = asyncio.create_task(peer.request("m", timeout=5))
    await process.written(1)

    process.say({"id": 1, "result": None})

    assert await pending == {}


async def test_a_request_not_answered_in_time_fails_and_a_late_answer_is_ignored(
    process: FakeProcess, peer: JsonRpcPeer
) -> None:
    with pytest.raises(RpcTimeoutError, match="m was not answered"):
        await peer.request("m", timeout=0.05)

    process.say({"id": 1, "result": {}})
    process.say({"id": 99, "result": {}})
    follow_up = asyncio.create_task(peer.request("n", timeout=5))
    await process.written(2)
    process.say({"id": 2, "result": {"ok": True}})

    assert await follow_up == {"ok": True}


async def test_when_the_connection_ends_the_waiting_request_fails_and_no_new_one_is_made(
    process: FakeProcess, peer: JsonRpcPeer
) -> None:
    pending = asyncio.create_task(peer.request("m", timeout=5))
    await process.written(1)

    process.hang_up()

    with pytest.raises(RpcClosedError):
        await pending
    assert peer.closed
    with pytest.raises(RpcClosedError):
        await peer.request("again", timeout=5)


async def test_a_process_that_takes_no_more_input_is_a_closed_connection(
    process: FakeProcess, peer: JsonRpcPeer
) -> None:
    process.send_error = BrokenPipeError("pipe closed")

    with pytest.raises(RpcClosedError):
        await peer.request("m", timeout=5)
    with pytest.raises(RpcClosedError):
        await peer.notify("n")


async def test_a_notification_is_sent_without_an_id_and_without_params_when_it_has_none(
    process: FakeProcess, peer: JsonRpcPeer
) -> None:
    await peer.notify("initialized")
    await peer.notify("other", {"a": 1})

    assert process.sent == [{"method": "initialized"}, {"method": "other", "params": {"a": 1}}]


# -- notifications -----------------------------------------------------------------------------------------------------------


async def test_notifications_arrive_in_wire_order_and_are_observed_as_they_are_read(
    process: FakeProcess, peer: JsonRpcPeer, watcher: _Watcher
) -> None:
    for number in range(3):
        process.say({"method": "tick", "params": {"n": number}})
    process.hang_up()

    received = [n async for n in peer.notifications(5) if n is not None]

    assert [n.params["n"] for n in received] == [0, 1, 2]
    assert [n.params["n"] for n in watcher.seen] == [0, 1, 2]


async def test_a_notification_without_params_has_empty_ones(process: FakeProcess, peer: JsonRpcPeer) -> None:
    process.say({"method": "bare"})
    process.hang_up()

    assert [n async for n in peer.notifications(5) if n is not None] == [Notification("bare", {})]


async def test_the_stream_ticks_while_the_server_is_quiet(peer: JsonRpcPeer) -> None:
    stream = peer.notifications(0.02)

    assert await anext(stream) is None
    await stream.aclose()


async def test_an_observer_that_breaks_costs_the_turn_nothing(
    process: FakeProcess, peer: JsonRpcPeer, watcher: _Watcher, caplog: pytest.LogCaptureFixture
) -> None:
    watcher.failing = True
    process.say({"method": "tick", "params": {"n": 1}})
    process.hang_up()

    with caplog.at_level(logging.WARNING):
        received = [n async for n in peer.notifications(5) if n is not None]

    assert [n.method for n in received] == ["tick"]
    assert "codex_rpc_observer_failed" in caplog.text


async def test_garbage_on_the_wire_is_skipped_and_what_follows_still_arrives(
    process: FakeProcess, peer: JsonRpcPeer, caplog: pytest.LogCaptureFixture
) -> None:
    process.say("this is not json")
    process.say("[1, 2, 3]")
    process.say({"neither": "request nor notification"})
    process.say({"method": "real", "params": {}})
    process.hang_up()

    with caplog.at_level(logging.WARNING):
        received = [n async for n in peer.notifications(5) if n is not None]

    assert [n.method for n in received] == ["real"]
    assert "codex_rpc_non_json_line" in caplog.text
    assert "this is not json" not in caplog.text


async def test_discarding_drops_what_nobody_read_and_keeps_the_end_of_the_connection(
    process: FakeProcess, peer: JsonRpcPeer
) -> None:
    process.say({"method": "late", "params": {}})
    process.hang_up()
    await asyncio.sleep(0.05)

    peer.discard_notifications()

    assert [n async for n in peer.notifications(5)] == []


# -- the requests of the server ---------------------------------------------------------------------------------------------


async def test_a_request_of_the_server_is_answered_with_what_the_handler_returns(process: FakeProcess) -> None:
    async def handler(method: str, params: Mapping[str, object]) -> Mapping[str, object]:
        return {"method": method, "echo": dict(params)}

    connection = JsonRpcPeer(process, on_server_request=handler, observe=lambda _: None)  # type: ignore[arg-type]
    try:
        process.say({"id": "abc", "method": "item/commandExecution/requestApproval", "params": {"command": "ls"}})
        await process.written(1)

        assert process.sent == [
            {
                "id": "abc",
                "result": {"method": "item/commandExecution/requestApproval", "echo": {"command": "ls"}},
            }
        ]
    finally:
        await connection.aclose()


async def test_a_handler_that_raises_an_rpc_error_answers_with_that_error(process: FakeProcess) -> None:
    async def handler(method: str, params: Mapping[str, object]) -> Mapping[str, object]:
        raise RpcError(-32601, "Method not supported")

    connection = JsonRpcPeer(process, on_server_request=handler, observe=lambda _: None)  # type: ignore[arg-type]
    try:
        process.say({"id": 7, "method": "x", "params": {}})
        await process.written(1)

        assert process.sent == [{"id": 7, "error": {"code": -32601, "message": "Method not supported"}}]
    finally:
        await connection.aclose()


async def test_a_handler_that_breaks_answers_with_an_internal_error_that_does_not_leak_its_message(
    process: FakeProcess, caplog: pytest.LogCaptureFixture
) -> None:
    async def handler(method: str, params: Mapping[str, object]) -> Mapping[str, object]:
        raise RuntimeError("secret detail")

    connection = JsonRpcPeer(process, on_server_request=handler, observe=lambda _: None)  # type: ignore[arg-type]
    try:
        with caplog.at_level(logging.WARNING):
            process.say({"id": 8, "method": "x", "params": {}})
            await process.written(1)

        assert process.sent == [{"id": 8, "error": {"code": -32603, "message": "Internal error"}}]
        assert "codex_rpc_server_request_failed" in caplog.text
    finally:
        await connection.aclose()


async def test_a_person_taking_minutes_to_decide_never_stops_the_reader(process: FakeProcess) -> None:
    release = asyncio.Event()

    async def handler(method: str, params: Mapping[str, object]) -> Mapping[str, object]:
        await release.wait()
        return {"decision": "accept"}

    connection = JsonRpcPeer(process, on_server_request=handler, observe=lambda _: None)  # type: ignore[arg-type]
    try:
        process.say({"id": 1, "method": "ask", "params": {}})
        process.say({"method": "while.waiting", "params": {}})
        stream = connection.notifications(5)

        assert (await anext(stream)).method == "while.waiting"  # type: ignore[union-attr]
        assert process.sent == []
        release.set()
        await process.written(1)
        assert process.sent == [{"id": 1, "result": {"decision": "accept"}}]
        await stream.aclose()
    finally:
        await connection.aclose()


async def test_closing_the_peer_cancels_a_request_of_the_server_that_is_still_waiting(process: FakeProcess) -> None:
    cancelled = asyncio.Event()

    async def handler(method: str, params: Mapping[str, object]) -> Mapping[str, object]:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {}

    connection = JsonRpcPeer(process, on_server_request=handler, observe=lambda _: None)  # type: ignore[arg-type]
    process.say({"id": 1, "method": "ask", "params": {}})
    await asyncio.sleep(0.05)

    await connection.aclose()

    assert cancelled.is_set()
    assert process.sent == []
    assert connection.closed
    await connection.aclose()
