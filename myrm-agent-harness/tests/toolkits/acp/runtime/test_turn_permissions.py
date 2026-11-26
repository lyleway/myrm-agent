"""TurnPermissions: what a backend can rely on when it asks the host's broker during a turn."""

from __future__ import annotations

import asyncio

import pytest

from myrm_agent_harness.toolkits.acp.runtime.turn_permissions import TurnPermissions
from myrm_agent_harness.toolkits.acp.types import PermissionRequest, PermissionVerdict

_REQUEST = PermissionRequest(
    agent="claude", session_id="chat-1", tool_name="Bash", kind="execute", title="Bash: ls", input={"command": "ls"}
)


class _Broker:
    """Answers when ``release`` is set (at once when it already is), and records what happened to each request."""

    def __init__(self, verdict: PermissionVerdict | Exception | None = None, *, wait: bool = False) -> None:
        self.verdict = verdict if verdict is not None else PermissionVerdict(allowed=True)
        self.release = asyncio.Event()
        if not wait:
            self.release.set()
        self.asked = 0
        self.cancelled = 0

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked += 1
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


class _RoutingBroker:
    """The first request goes to ``first``, the second to ``second``: parallel tool calls of one turn."""

    def __init__(self, first: _Broker, second: _Broker) -> None:
        self._brokers = [first, second]

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        return await self._brokers.pop(0).decide(request)


class TestTheVerdict:
    async def test_the_brokers_verdict_is_passed_through(self) -> None:
        verdict = PermissionVerdict(allowed=False, message="No.")
        async with asyncio.timeout(None) as clock:
            assert await TurnPermissions(_Broker(verdict), clock).decide(_REQUEST) == verdict

    async def test_a_failing_broker_is_a_refusal_and_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        async with asyncio.timeout(None) as clock:
            with caplog.at_level("WARNING"):
                verdict = await TurnPermissions(_Broker(RuntimeError("down")), clock).decide(_REQUEST)

        assert verdict.allowed is False
        assert any(
            "permission_broker_failed" in record.message and "tool=Bash" in record.message for record in caplog.records
        )


class TestTheTurnEnding:
    async def test_a_request_still_waiting_when_the_turn_ends_is_cancelled_and_refused(self) -> None:
        broker = _Broker(wait=True)
        async with asyncio.timeout(None) as clock:
            permissions = TurnPermissions(broker, clock)
            waiting = asyncio.create_task(permissions.decide(_REQUEST))
            await asyncio.sleep(0.05)

            permissions.close()
            verdict = await asyncio.wait_for(waiting, 5)

        assert verdict.allowed is False
        assert "turn ended" in (verdict.message or "")
        assert (broker.asked, broker.cancelled) == (1, 1)

    async def test_a_request_that_ends_after_the_turns_clock_was_left_is_still_refused(self) -> None:
        """The request wakes up after the turn: it must not try to set the clock the turn has already left."""
        broker = _Broker(wait=True)
        async with asyncio.timeout(30) as clock:
            permissions = TurnPermissions(broker, clock)
            waiting = asyncio.create_task(permissions.decide(_REQUEST))
            await asyncio.sleep(0.05)
            permissions.close()

        verdict = await asyncio.wait_for(waiting, 5)

        assert verdict.allowed is False
        assert "turn ended" in (verdict.message or "")

    async def test_a_request_after_the_turn_ended_is_refused_without_asking_anyone(self) -> None:
        broker = _Broker()
        async with asyncio.timeout(None) as clock:
            permissions = TurnPermissions(broker, clock)
            permissions.close()

            verdict = await permissions.decide(_REQUEST)

        assert verdict.allowed is False
        assert broker.asked == 0

    async def test_a_caller_that_is_cancelled_is_not_turned_into_a_verdict(self) -> None:
        broker = _Broker(wait=True)
        async with asyncio.timeout(None) as clock:
            waiting = asyncio.create_task(TurnPermissions(broker, clock).decide(_REQUEST))
            await asyncio.sleep(0.05)

            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting

        assert broker.cancelled == 1


class TestTheTimeLimit:
    """The limit is what the agent spends working: a person's time is not the agent's."""

    async def test_the_time_a_person_takes_does_not_count(self) -> None:
        broker = _Broker(wait=True)
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(0.6) as clock:
            permissions = TurnPermissions(broker, clock)
            await asyncio.sleep(0.2)
            loop.call_later(1.0, broker.release.set)

            verdict = await permissions.decide(_REQUEST)
            await asyncio.sleep(0.2)

        assert verdict.allowed is True
        assert not clock.expired()

    async def test_what_is_left_of_the_limit_after_the_wait_is_the_same_as_before_it(self) -> None:
        broker = _Broker(wait=True)
        loop = asyncio.get_running_loop()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.6) as clock:
                permissions = TurnPermissions(broker, clock)
                await asyncio.sleep(0.3)
                loop.call_later(0.5, broker.release.set)

                await permissions.decide(_REQUEST)
                # 0.3 s of the limit were spent before the wait: 0.3 s are left, so 0.45 s of work run out of it.
                await asyncio.sleep(0.45)

    async def test_requests_waiting_side_by_side_lift_the_limit_once_and_restore_it_when_the_last_is_answered(
        self,
    ) -> None:
        first, second = _Broker(wait=True), _Broker(wait=True)
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(0.8) as clock:
            # One TurnPermissions per turn; two parallel tool calls ask through it.
            permissions = TurnPermissions(_RoutingBroker(first, second), clock)
            await asyncio.sleep(0.1)
            loop.call_later(0.5, first.release.set)
            loop.call_later(1.3, second.release.set)

            verdicts = await asyncio.gather(permissions.decide(_REQUEST), permissions.decide(_REQUEST))
            await asyncio.sleep(0.2)

        assert [verdict.allowed for verdict in verdicts] == [True, True]
        assert not clock.expired()

    async def test_a_limit_that_already_ran_out_is_left_alone(self) -> None:
        broker = _Broker()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1) as clock:
                permissions = TurnPermissions(broker, clock)
                await asyncio.sleep(0.5)
                await permissions.decide(_REQUEST)

        assert clock.expired()
