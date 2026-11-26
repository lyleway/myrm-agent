"""What the turn body does when a step of its setup goes wrong, and what it carries into the session.

A request that cannot be honored (an archive that cannot be restored, a search service that is not there) ends the
turn with an error event before anything is pumped. A replay marker that cannot be written does not stop the turn,
and a history mode the chat's thread asks for reaches the session's context.
"""

from __future__ import annotations

import logging

import pytest
from myrm_agent_harness.runtime.context.session import HISTORY_MODE_CONTEXT_KEY
from myrm_agent_harness.utils.runtime.cancellation import CancellationRegistry

from app.services.agent.params import ArchiveRestoreRequestError
from tests.services.agent.stream_session.turn_body_support import (
    MESSAGE_ID,
    TurnBodyRun,
    run_turn_body,
    stub_turn_body,
)

_TURN_BODY_LOGGER = "app.services.agent.stream_session.orchestrator_turn_body"


def _assert_ended_with_error(run: TurnBodyRun, detail: str) -> None:
    """The one error event carries the reason; the stream is closed, nothing was pumped and Stop has nothing to cancel."""
    run.buffer.end_stream.assert_awaited_once()
    run.buffer.append.assert_awaited_once()
    (event,) = run.buffer.append.await_args.args
    assert detail in event
    assert MESSAGE_ID in event
    assert run.pumped == []
    assert CancellationRegistry.cancel(MESSAGE_ID) is False


class TestARequestThatCannotBeHonored:
    async def test_an_archive_that_cannot_be_restored_ends_the_turn_with_its_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = stub_turn_body(monkeypatch, build_error=ArchiveRestoreRequestError("Archive entry is missing"))

        await run_turn_body(run)

        run.record_failure.assert_awaited_once_with("archive_restore_invalid")
        _assert_ended_with_error(run, "Archive entry is missing")

    async def test_a_search_service_that_is_not_there_ends_the_turn_with_its_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = stub_turn_body(monkeypatch, unavailable_detail="Search is not available")

        await run_turn_body(run)

        run.record_failure.assert_awaited_once_with("server_error")
        _assert_ended_with_error(run, "Search is not available")


class TestWhatTheTurnCarriesIntoTheSession:
    async def test_a_marker_that_cannot_be_written_does_not_stop_the_turn(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        run = stub_turn_body(monkeypatch, marker_error=RuntimeError("database is locked"))

        with caplog.at_level(logging.WARNING, logger=_TURN_BODY_LOGGER):
            await run_turn_body(run)

        assert len(run.pumped) == 1
        assert run.pumped[0].turn_marker_id is None
        assert "Turn marker write skipped: database is locked" in caplog.text

    async def test_a_marker_that_was_written_travels_with_the_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = stub_turn_body(monkeypatch)

        await run_turn_body(run)

        assert run.pumped[0].turn_marker_id == "marker-1"

    async def test_a_history_mode_the_thread_asks_for_reaches_the_session_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = stub_turn_body(monkeypatch, history_mode="replace")

        await run_turn_body(run)

        assert run.sessions[0].extra_context[HISTORY_MODE_CONTEXT_KEY] == "replace"

    async def test_a_turn_that_declares_no_history_mode_leaves_the_context_without_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = stub_turn_body(monkeypatch)

        await run_turn_body(run)

        assert HISTORY_MODE_CONTEXT_KEY not in run.sessions[0].extra_context
