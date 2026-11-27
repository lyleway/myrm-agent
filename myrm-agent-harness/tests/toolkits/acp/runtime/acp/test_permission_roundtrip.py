"""An ACP agent's permission requests and file calls, answered over the real SDK wire.

The scripted agent (``_mock_acp_agent.py``) is a real SDK agent: it sends ``session/request_permission`` and
``fs/write_text_file`` through the SDK and reports what came back, so these tests
cover what the runtime hands to the SDK and how the SDK's answer reads on the agent's side.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    PermissionBroker,
    PermissionMode,
    PermissionRequest,
    PermissionVerdict,
    RuntimeEvent,
    RuntimeEventType,
)

from ._fakes import make_pool

_PERMISSION_DENIED = "-32001"
_INVALID_PARAMS = "-32602"


@dataclass
class _Broker:
    """A broker that answers every request the same way and remembers what it was asked."""

    verdict: PermissionVerdict = field(default_factory=lambda: PermissionVerdict(allowed=True))
    asked: list[PermissionRequest] = field(default_factory=list)

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked.append(request)
        return self.verdict


async def _report(
    pool: RuntimePool, workspace: Path, verb: str, args: dict[str, object] | None, *, broker: PermissionBroker | None
) -> str:
    """The agent's report of one scripted call: ``verb`` with ``args`` (when it takes any) as the prompt."""
    text: list[str] = []
    async for event in pool.run_turn(
        "mock",
        f"/{verb}" if args is None else f"/{verb} {json.dumps(args)}",
        session_id="chat-1",
        workspace=str(workspace),
        permissions=broker,
    ):
        if event.type is RuntimeEventType.ERROR:
            pytest.fail(f"the turn failed: {event.data}")
        if event.type is RuntimeEventType.TEXT_DELTA:
            text.append(str(event.data["content"]))
    return "".join(text)


async def _permission(
    mode: PermissionMode, workspace: Path, args: dict[str, object], broker: PermissionBroker | None
) -> str:
    pool = make_pool(permission_mode=mode)
    try:
        return await _report(pool, workspace, "permission", args, broker=broker)
    finally:
        await pool.close_all()


class TestPermissionRequests:
    async def test_an_edit_the_broker_allows_is_answered_with_the_agents_allow_option(self, tmp_path: Path) -> None:
        broker = _Broker()

        report = await _permission("safe", tmp_path, {"kind": "edit", "title": "Edit notes.txt"}, broker)

        assert report == "[permission] selected allow-once"
        assert len(broker.asked) == 1

    async def test_an_edit_the_broker_refuses_is_answered_with_the_agents_reject_option(self, tmp_path: Path) -> None:
        broker = _Broker(PermissionVerdict(allowed=False))

        report = await _permission("safe", tmp_path, {"kind": "execute", "title": "Run the tests"}, broker)

        assert report == "[permission] selected reject-once"

    async def test_a_request_with_nobody_to_ask_is_refused(self, tmp_path: Path) -> None:
        report = await _permission("safe", tmp_path, {"kind": "edit", "title": "Edit notes.txt"}, None)

        assert report == "[permission] selected reject-once"

    @pytest.mark.parametrize("kind", ["read", "search", "think"])
    async def test_a_request_that_only_reads_is_allowed_without_asking(self, tmp_path: Path, kind: str) -> None:
        broker = _Broker(PermissionVerdict(allowed=False))

        report = await _permission("safe", tmp_path, {"kind": kind, "title": "Look"}, broker)

        assert report == "[permission] selected allow-once"
        assert broker.asked == []

    async def test_a_request_without_a_kind_is_asked_about(self, tmp_path: Path) -> None:
        broker = _Broker()

        await _permission("safe", tmp_path, {"title": "Do something"}, broker)

        assert [request.kind for request in broker.asked] == ["other"]

    async def test_allow_all_allows_everything_without_asking(self, tmp_path: Path) -> None:
        broker = _Broker(PermissionVerdict(allowed=False))

        report = await _permission("allow_all", tmp_path, {"kind": "execute", "title": "Run it"}, broker)

        assert report == "[permission] selected allow-once"
        assert broker.asked == []

    async def test_the_broker_is_told_what_the_agent_wants_to_do(self, tmp_path: Path) -> None:
        broker = _Broker()
        link = tmp_path / "link.txt"
        link.symlink_to(tmp_path / "real.txt")

        await _permission(
            "safe",
            tmp_path,
            {
                "kind": "edit",
                "title": "Edit files",
                "raw_input": {"command": "sed -i s/a/b/ notes.txt"},
                "locations": ["notes.txt", "link.txt"],
                "diff_path": "notes.txt",
            },
            broker,
        )

        (request,) = broker.asked
        assert (request.agent, request.session_id, request.kind, request.title) == (
            "mock",
            "chat-1",
            "edit",
            "Edit files",
        )
        assert request.input == {"command": "sed -i s/a/b/ notes.txt"}
        assert request.tool_call_id == "call-1"
        # Relative to the workspace, a link resolved, and a file named twice listed once.
        assert request.paths == (str((tmp_path / "notes.txt").resolve()), str((tmp_path / "real.txt").resolve()))


class TestFileWrites:
    async def _write(
        self, mode: PermissionMode, workspace: Path, args: dict[str, object], broker: PermissionBroker | None
    ) -> str:
        pool = make_pool(permission_mode=mode)
        try:
            return await _report(pool, workspace, "write", args, broker=broker)
        finally:
            await pool.close_all()

    async def test_a_write_the_broker_allows_lands_on_disk_byte_for_byte(self, tmp_path: Path) -> None:
        broker = _Broker()

        report = await self._write("safe", tmp_path, {"path": "out/a.txt", "content": "one\r\ntwo\r\n"}, broker)

        assert report == "[write] ok"
        assert (tmp_path / "out" / "a.txt").read_bytes() == b"one\r\ntwo\r\n"
        assert [request.paths for request in broker.asked] == [(str((tmp_path / "out" / "a.txt").resolve()),)]

    async def test_a_write_the_broker_refuses_is_an_error_to_the_agent_and_writes_nothing(self, tmp_path: Path) -> None:
        report = await self._write(
            "safe", tmp_path, {"path": "a.txt", "content": "x"}, _Broker(PermissionVerdict(allowed=False))
        )

        assert report == f'[write] error {_PERMISSION_DENIED} Permission denied {{"path": "a.txt"}}'
        assert not (tmp_path / "a.txt").exists()

    async def test_a_write_with_nobody_to_ask_is_refused(self, tmp_path: Path) -> None:
        report = await self._write("safe", tmp_path, {"path": "a.txt", "content": "x"}, None)

        assert report == f'[write] error {_PERMISSION_DENIED} Permission denied {{"path": "a.txt"}}'
        assert not (tmp_path / "a.txt").exists()

    async def test_allow_all_writes_without_asking(self, tmp_path: Path) -> None:
        broker = _Broker(PermissionVerdict(allowed=False))

        report = await self._write("allow_all", tmp_path, {"path": "a.txt", "content": "x"}, broker)

        assert report == "[write] ok"
        assert broker.asked == []

    async def test_a_write_outside_the_workspace_is_refused_before_anyone_is_asked(self, tmp_path: Path) -> None:
        broker = _Broker()
        outside = tmp_path.parent / "elsewhere.txt"

        report = await self._write("allow_all", tmp_path, {"path": str(outside), "content": "x"}, broker)

        assert report.startswith(f"[write] error {_INVALID_PARAMS} Invalid params")
        assert not outside.exists()
        assert broker.asked == []

    async def test_a_write_the_person_allowed_with_the_edit_is_not_asked_about_again(self, tmp_path: Path) -> None:
        broker = _Broker()
        pool = make_pool(permission_mode="safe")
        try:
            report = await _report(pool, tmp_path, "edit", {"path": "a.txt", "content": "x"}, broker=broker)
        finally:
            await pool.close_all()

        assert report == "[permission] selected allow-once [write] ok"
        assert (tmp_path / "a.txt").read_text() == "x"
        assert [request.title for request in broker.asked] == ["Edit a.txt"]

    async def test_what_a_person_allowed_in_one_turn_is_not_allowed_in_the_next(self, tmp_path: Path) -> None:
        pool = make_pool(permission_mode="safe")
        try:
            await _report(pool, tmp_path, "edit", {"path": "a.txt", "content": "x"}, broker=_Broker())
            report = await _report(
                pool,
                tmp_path,
                "write",
                {"path": "a.txt", "content": "y"},
                broker=_Broker(PermissionVerdict(allowed=False)),
            )
        finally:
            await pool.close_all()

        assert report == f'[write] error {_PERMISSION_DENIED} Permission denied {{"path": "a.txt"}}'
        assert (tmp_path / "a.txt").read_text() == "x"


class TestWhatTheHostOffers:
    async def test_the_agent_is_offered_file_writes_and_not_file_reads(self, tmp_path: Path) -> None:
        pool = make_pool(permission_mode="safe")
        try:
            report = await _report(pool, tmp_path, "capabilities", None, broker=None)
        finally:
            await pool.close_all()

        offered = json.loads(report.removeprefix("[capabilities] "))
        assert offered["fs"] == {"read_text_file": False, "write_text_file": True}
        assert offered["terminal"] is False


class TestWaitingForAPerson:
    async def test_the_time_a_person_takes_is_not_the_time_the_agent_has(self, tmp_path: Path) -> None:
        # A turn limit a loaded machine can still start the agent within, and an answer that comes after it has run out.
        agent_seconds = 3

        class _SlowBroker:
            async def decide(self, request: PermissionRequest) -> PermissionVerdict:
                await asyncio.sleep(agent_seconds + 1)
                return PermissionVerdict(allowed=True)

        pool = make_pool(permission_mode="safe", timeout_seconds=agent_seconds)
        try:
            report = await _report(
                pool, tmp_path, "permission", {"kind": "edit", "title": "Edit"}, broker=_SlowBroker()
            )
        finally:
            await pool.close_all()

        assert report == "[permission] selected allow-once"


class TestCancellingWhileAsked:
    async def test_the_waiting_request_is_answered_as_cancelled_and_the_agent_ends_its_prompt(
        self, tmp_path: Path
    ) -> None:
        asked = asyncio.Event()
        withdrawn = asyncio.Event()

        class _NeverAnswering:
            async def decide(self, request: PermissionRequest) -> PermissionVerdict:
                asked.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    withdrawn.set()
                    raise
                raise AssertionError("unreachable")

        pool = make_pool(permission_mode="safe")
        events: list[RuntimeEvent] = []

        async def run() -> None:
            async for event in pool.run_turn(
                "mock",
                f"/permission {json.dumps({'kind': 'edit', 'title': 'Edit'})}",
                session_id="chat-1",
                workspace=str(tmp_path),
                permissions=_NeverAnswering(),
            ):
                events.append(event)

        try:
            turn = asyncio.create_task(run())
            await asyncio.wait_for(asked.wait(), 30)
            await pool.cancel("mock", "chat-1")
            await asyncio.wait_for(turn, 30)
        finally:
            await pool.close_all()

        assert withdrawn.is_set()
        assert [event.type for event in events if event.type is RuntimeEventType.ERROR] == []
        reported = "".join(str(event.data["content"]) for event in events if event.type is RuntimeEventType.TEXT_DELTA)
        assert reported == "[permission] cancelled"
        assert [event.data["stop_reason"] for event in events if event.type is RuntimeEventType.DONE] == ["cancelled"]
