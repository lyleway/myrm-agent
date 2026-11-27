"""What the host answers when Codex's app server asks: each kind of request, and every way of not being able to answer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from myrm_agent_harness.toolkits.acp.runtime.codex.approvals import ApprovalResponder
from myrm_agent_harness.toolkits.acp.runtime.codex.items import ItemBook
from myrm_agent_harness.toolkits.acp.runtime.codex.rpc import METHOD_NOT_FOUND, Notification, RpcError
from myrm_agent_harness.toolkits.acp.runtime.turn_permissions import TurnPermissions
from myrm_agent_harness.toolkits.acp.types import PermissionRequest, PermissionVerdict
from tests.toolkits.acp._brokers import AnsweringBroker

_COMMAND = "item/commandExecution/requestApproval"
_FILE_CHANGE = "item/fileChange/requestApproval"
_PERMISSIONS = "item/permissions/requestApproval"
_ELICITATION = "mcpServer/elicitation/request"


class _FailingBroker:
    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        raise RuntimeError("the broker broke")


@pytest.fixture
async def clock() -> AsyncIterator[asyncio.Timeout]:
    async with asyncio.timeout(None) as running:
        yield running


def _responder(items: ItemBook | None = None) -> ApprovalResponder:
    return ApprovalResponder("codex", items or ItemBook())


def _begin(
    responder: ApprovalResponder,
    broker: AnsweringBroker | _FailingBroker | None,
    clock: asyncio.Timeout,
    mode: Literal["safe", "allow_all"] = "safe",
) -> None:
    responder.begin_turn("chat-1", None if broker is None else TurnPermissions(broker, clock), mode)


# -- the requests --------------------------------------------------------------------------------------------------------------


async def test_a_command_is_put_to_the_broker_as_an_execution_with_what_the_server_knows_of_it(
    clock: asyncio.Timeout,
) -> None:
    broker, responder = AnsweringBroker(True), _responder()
    _begin(responder, broker, clock)
    params = {
        "itemId": "cmd-1",
        "command": "/bin/zsh -lc 'curl example.com'\nsecond line",
        "cwd": "/w",
        "reason": "fetch the page",
        "networkApprovalContext": {"host": "example.com", "protocol": "https"},
    }

    answer = await responder.handle(_COMMAND, params)

    assert answer == {"decision": "accept"}
    (request,) = broker.asked
    assert (request.agent, request.session_id, request.tool_name, request.kind) == (
        "codex",
        "chat-1",
        "command_execution",
        "execute",
    )
    assert request.title == "command_execution: /bin/zsh -lc 'curl example.com'"
    assert request.tool_call_id == "cmd-1"
    assert request.input == {
        "command": params["command"],
        "cwd": "/w",
        "reason": "fetch the page",
        "network_host": "example.com",
    }


async def test_a_refusal_is_a_decline(clock: asyncio.Timeout) -> None:
    responder = _responder()
    _begin(responder, AnsweringBroker(False), clock)

    assert await responder.handle(_COMMAND, {"command": "rm -rf /"}) == {"decision": "decline"}


def _announce_patch(items: ItemBook, changes: list[dict[str, object]], item_id: str = "f-1") -> None:
    item = {"type": "fileChange", "id": item_id, "changes": changes}
    items.observe(Notification("item/started", {"turnId": "turn-1", "item": item}))


async def test_a_patch_is_put_to_the_broker_as_an_edit_of_the_files_the_item_announced(clock: asyncio.Timeout) -> None:
    items, broker = ItemBook(), AnsweringBroker(True)
    responder = _responder(items)
    _begin(responder, broker, clock)
    _announce_patch(
        items,
        [{"path": "a.py", "kind": {"type": "update"}, "diff": "x" * 5000}, {"path": "b.py", "kind": {"type": "add"}}],
    )

    answer = await responder.handle(
        _FILE_CHANGE, {"itemId": "f-1", "turnId": "turn-1", "reason": "needs a write", "grantRoot": "/w"}
    )

    assert answer == {"decision": "accept"}
    (request,) = broker.asked
    assert (request.tool_name, request.kind, request.paths) == ("file_change", "edit", ("a.py", "b.py"))
    assert request.title == "file_change: update a.py, add b.py"
    assert request.input["reason"] == "needs a write"
    assert request.input["grant_root"] == "/w"
    changes = request.input["changes"]
    assert isinstance(changes, list)
    assert [c["path"] for c in changes] == ["a.py", "b.py"]
    assert len(changes[0]["diff"]) == 2000


@pytest.mark.parametrize(
    ("changes", "kind"),
    [
        ([{"path": "a", "kind": {"type": "delete"}}, {"path": "b", "kind": {"type": "delete"}}], "delete"),
        ([{"path": "a", "kind": {"type": "delete"}}, {"path": "b", "kind": {"type": "add"}}], "edit"),
        ([{"path": "a", "kind": {"type": "update", "move_path": "c"}}], "move"),
        ([], "edit"),
    ],
)
async def test_the_kind_of_a_patch_says_whether_it_deletes_or_moves(
    clock: asyncio.Timeout, changes: list[dict[str, object]], kind: str
) -> None:
    items, broker = ItemBook(), AnsweringBroker(True)
    responder = _responder(items)
    _begin(responder, broker, clock)
    _announce_patch(items, changes)

    await responder.handle(_FILE_CHANGE, {"itemId": "f-1", "turnId": "turn-1"})

    assert broker.asked[0].kind == kind


async def test_a_huge_patch_is_shown_as_a_bounded_excerpt(clock: asyncio.Timeout) -> None:
    items, broker = ItemBook(), AnsweringBroker(True)
    responder = _responder(items)
    _begin(responder, broker, clock)
    _announce_patch(items, [{"path": f"f{n}.py", "kind": {"type": "add"}} for n in range(50)])

    await responder.handle(_FILE_CHANGE, {"itemId": "f-1", "turnId": "turn-1"})

    (request,) = broker.asked
    assert len(request.input["changes"]) == 20  # type: ignore[arg-type]
    assert len(request.paths) == 20


async def test_a_patch_the_server_never_announced_is_still_asked_about(clock: asyncio.Timeout) -> None:
    broker, responder = AnsweringBroker(True), _responder()
    _begin(responder, broker, clock)

    answer = await responder.handle(_FILE_CHANGE, {"itemId": "ghost", "turnId": "turn-1"})

    assert answer == {"decision": "accept"}
    assert broker.asked[0].paths == ()


@pytest.mark.parametrize(("allowed", "granted"), [(True, True), (False, False)])
async def test_a_request_for_more_permissions_grants_what_was_asked_for_the_turn_or_nothing(
    clock: asyncio.Timeout, allowed: bool, granted: bool
) -> None:
    broker, responder = AnsweringBroker(allowed), _responder()
    _begin(responder, broker, clock)
    permissions = {"network": {"enabled": True}}

    answer = await responder.handle(_PERMISSIONS, {"permissions": permissions, "reason": "needs the network"})

    assert answer == {"permissions": permissions if granted else {}, "scope": "turn"}
    (request,) = broker.asked
    assert (request.tool_name, request.kind, request.title) == (
        "request_permissions",
        "other",
        "request_permissions: needs the network",
    )
    assert request.input == {"permissions": permissions, "reason": "needs the network"}


async def test_the_approval_of_an_mcp_tool_call_is_put_to_the_broker_under_the_tools_name(
    clock: asyncio.Timeout,
) -> None:
    broker, responder = AnsweringBroker(True), _responder()
    _begin(responder, broker, clock)
    params = {
        "serverName": "echo",
        "_meta": {"codex_approval_kind": "mcp_tool_call", "tool_name": "ping", "tool_params": {"text": "hi"}},
    }

    answer = await responder.handle(_ELICITATION, params)

    assert answer == {"action": "accept", "content": None}
    (request,) = broker.asked
    assert (request.tool_name, request.kind, request.input) == ("mcp__echo__ping", "other", {"text": "hi"})


async def test_any_other_question_of_an_mcp_server_is_declined_without_asking_anyone(clock: asyncio.Timeout) -> None:
    broker, responder = AnsweringBroker(True), _responder()
    _begin(responder, broker, clock)

    answer = await responder.handle(_ELICITATION, {"serverName": "echo", "_meta": {}, "message": "your name?"})

    assert answer == {"action": "decline", "content": None}
    assert broker.asked == []


async def test_a_question_for_the_user_gets_no_answers_and_a_tool_call_for_the_host_fails(
    clock: asyncio.Timeout,
) -> None:
    responder = _responder()
    _begin(responder, AnsweringBroker(True), clock)

    assert await responder.handle("item/tool/requestUserInput", {"questions": []}) == {"answers": {}}
    assert await responder.handle("item/tool/call", {"tool": "x"}) == {"contentItems": [], "success": False}


async def test_a_request_the_host_does_not_know_is_method_not_found() -> None:
    with pytest.raises(RpcError) as raised:
        await _responder().handle("item/never/heardOf", {})

    assert raised.value.code == METHOD_NOT_FOUND


# -- the requests nobody can answer ----------------------------------------------------------------------------------------------


async def test_without_a_broker_every_request_is_declined(clock: asyncio.Timeout) -> None:
    responder = _responder()
    _begin(responder, None, clock)

    assert await responder.handle(_COMMAND, {"command": "ls"}) == {"decision": "decline"}
    assert await responder.handle(_FILE_CHANGE, {"itemId": "f", "turnId": "t"}) == {"decision": "decline"}
    assert await responder.handle(_PERMISSIONS, {"permissions": {"x": 1}}) == {"permissions": {}, "scope": "turn"}


async def test_under_allow_all_nothing_is_asked_and_a_stray_request_is_declined(clock: asyncio.Timeout) -> None:
    broker, responder = AnsweringBroker(True), _responder()
    _begin(responder, broker, clock, mode="allow_all")

    assert await responder.handle(_COMMAND, {"command": "ls"}) == {"decision": "decline"}
    assert broker.asked == []


async def test_a_broker_that_breaks_is_a_refusal(clock: asyncio.Timeout) -> None:
    responder = _responder()
    _begin(responder, _FailingBroker(), clock)

    assert await responder.handle(_COMMAND, {"command": "ls"}) == {"decision": "decline"}


async def test_a_request_outside_a_turn_cancels_it() -> None:
    responder = _responder()

    assert await responder.handle(_COMMAND, {"command": "ls"}) == {"decision": "cancel"}


async def test_a_request_after_the_turn_ended_cancels_it(clock: asyncio.Timeout) -> None:
    broker, responder = AnsweringBroker(True), _responder()
    _begin(responder, broker, clock)

    responder.end_turn()

    assert await responder.handle(_COMMAND, {"command": "ls"}) == {"decision": "cancel"}
    assert broker.asked == []


async def test_a_request_still_waiting_when_the_turn_ends_is_withdrawn_and_cancelled(clock: asyncio.Timeout) -> None:
    broker, responder = AnsweringBroker(None), _responder()
    _begin(responder, broker, clock)

    waiting = asyncio.create_task(responder.handle(_COMMAND, {"command": "ls"}))
    await broker.asked_event.wait()
    responder.end_turn()

    assert await asyncio.wait_for(waiting, 5) == {"decision": "cancel"}
    assert broker.withdrawn


async def test_ending_a_turn_that_never_began_is_harmless() -> None:
    _responder().end_turn()
