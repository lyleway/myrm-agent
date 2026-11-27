"""Unit tests: the plan panel judgment can fail, and a recorder dump reads back as typed events and panel rows."""

from __future__ import annotations

import pytest

from tests.support.plan_panel_probe import (
    OpenChat,
    PanelRow,
    PlanEvent,
    PlanRead,
    Step,
    Turn,
    check_turn,
    parse_turn,
    shown_in_turn,
    timeline,
)

_FIRST: list[Step] = [("Alpha", "in_progress"), ("Beta", "pending")]
_SECOND: list[Step] = [("Alpha", "completed"), ("Beta", "in_progress")]


def _plan(revision: int, steps: list[Step]) -> dict[str, object]:
    return {"revision": revision, "steps": [{"description": text, "status": status} for text, status in steps]}


def _row(at_ms: int, steps: list[Step], *, visible: bool = True, ended: bool = False) -> PanelRow:
    return PanelRow(at_ms, steps, visible, ended)


def _turn(events: list[PlanEvent], panel: list[PanelRow]) -> Turn:
    reads = [PlanRead(120, 150, 200, 1), PlanRead(950, 990, 200, 2)]
    return Turn(
        chat_id="chat-1",
        width=1280,
        events=events,
        panel=panel,
        plan_reads=reads,
        open_chats=[OpenChat(0, "chat-1")],
    )


def _two_writes() -> list[PlanEvent]:
    return [PlanEvent(100, 1, _FIRST), PlanEvent(900, 2, _SECOND)]


def test_a_recorder_dump_reads_back_as_events_and_rows_with_plan_step_statuses() -> None:
    raw = {
        "width": 1280,
        "events": [
            {
                "atMs": 100,
                "revision": 1,
                "todos": [{"content": "Alpha", "status": "in_progress"}, {"content": "Beta", "status": "cancelled"}],
            }
        ],
        "panel": [
            {
                "atMs": 150,
                "ended": False,
                "steps": [
                    {"text": "Alpha", "status": "in_progress", "visible": True},
                    {"text": "Beta", "status": "skipped", "visible": False},
                ],
            }
        ],
        "planReads": [
            {"atMs": 120, "doneMs": 140, "status": 200, "revision": 1},
            {"atMs": 900, "doneMs": None, "status": None, "revision": None},
        ],
        "chats": [{"atMs": 0, "chatId": None}, {"atMs": 60, "chatId": "chat-1"}],
    }

    turn = parse_turn("chat-1", raw)

    steps = [("Alpha", "in_progress"), ("Beta", "skipped")]
    assert turn.events == [PlanEvent(100, 1, steps)]
    assert turn.panel == [PanelRow(150, steps, False, False)]
    assert turn.plan_reads == [PlanRead(120, 140, 200, 1), PlanRead(900, None, None, None)]
    assert turn.open_chats == [OpenChat(0, None), OpenChat(60, "chat-1")]
    assert turn.width == 1280


def test_a_run_whose_plans_all_reached_the_panel_in_time_has_nothing_left_to_ask() -> None:
    turn = _turn(_two_writes(), [_row(180, _FIRST), _row(950, _SECOND), _row(2000, _SECOND, ended=True)])

    assert check_turn(turn, _plan(2, _SECOND)) == []
    assert "shown_in_turn=[True, True]" in timeline(turn)


def test_the_timeline_tells_when_each_plan_was_read_and_what_the_endpoint_served() -> None:
    line = timeline(_turn(_two_writes(), [_row(180, _FIRST)]))

    assert "event_at_ms=[100, 900]" in line
    assert "plan_reads(at,done,status,revision)=[(120, 150, 200, 1), (950, 990, 200, 2)]" in line
    assert "open_chats=[(0, 'chat-1')]" in line


def test_a_single_write_is_handed_back_for_another_attempt() -> None:
    turn = _turn([PlanEvent(100, 1, _FIRST)], [_row(180, _FIRST)])

    assert check_turn(turn, _plan(1, _FIRST)) == ["the model wrote the plan 1 time(s)"]


def test_an_earlier_plan_that_never_reached_the_panel_is_handed_back_for_another_attempt() -> None:
    turn = _turn(_two_writes(), [_row(950, _SECOND)])

    [problem] = check_turn(turn, _plan(2, _SECOND))

    assert problem.startswith("only the last of 2 plans reached the panel in time")


def test_a_skipped_revision_fails_the_run() -> None:
    turn = _turn([PlanEvent(100, 1, _FIRST), PlanEvent(900, 3, _SECOND)], [_row(950, _SECOND)])

    with pytest.raises(AssertionError, match="exactly one plan_update"):
        check_turn(turn, _plan(3, _SECOND))


def test_a_write_the_stream_never_announced_fails_the_run() -> None:
    turn = _turn([PlanEvent(100, 1, _FIRST)], [_row(180, _FIRST)])

    with pytest.raises(AssertionError, match="served plan"):
        check_turn(turn, _plan(2, _FIRST))


def test_a_plan_with_no_event_at_all_fails_the_run() -> None:
    with pytest.raises(AssertionError, match="served plan"):
        check_turn(_turn([], []), _plan(1, _FIRST))


def test_a_served_plan_that_differs_from_the_last_event_fails_the_run() -> None:
    turn = _turn([PlanEvent(100, 1, _FIRST)], [_row(180, _FIRST)])

    with pytest.raises(AssertionError, match="differs from the last event"):
        check_turn(turn, _plan(1, _SECOND))


def test_a_last_plan_shown_only_after_the_turn_ended_fails_the_run() -> None:
    turn = _turn(_two_writes(), [_row(180, _FIRST), _row(2000, _SECOND, ended=True)])

    with pytest.raises(AssertionError, match="never reached the panel") as failure:
        check_turn(turn, _plan(2, _SECOND))

    assert "plan_reads(at,done,status,revision)=" in str(failure.value)


def test_a_panel_that_listed_the_plan_before_the_event_arrived_is_not_credited() -> None:
    event = PlanEvent(100, 1, _FIRST)

    assert shown_in_turn(event, [_row(50, _FIRST)]) is None
    with pytest.raises(AssertionError, match="never reached the panel"):
        check_turn(_turn([event], [_row(50, _FIRST)]), _plan(1, _FIRST))


def test_a_plan_listed_out_of_the_user_s_sight_fails_the_run() -> None:
    turn = _turn([PlanEvent(100, 1, _FIRST)], [_row(180, _FIRST, visible=False)])

    with pytest.raises(AssertionError, match="out of the user's sight"):
        check_turn(turn, _plan(1, _FIRST))
