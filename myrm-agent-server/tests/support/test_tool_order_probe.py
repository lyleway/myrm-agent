"""Unit tests: what a live turn of conflicting tool calls leaves behind is read back as the order the steps ran in."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from tests.support.tool_order_probe import (
    BASH_STEP_KEY,
    BATCH_SHELL_TAG,
    STEP_LOG_NAME,
    WRITE_STEP_KEY,
    Step,
    bash_tags_in,
    batch_prompt,
    ran_in_one_message,
    read_steps,
    started_tags,
    step_keys_in,
    step_script,
    stored_stop_reason,
    tool_round_ends_ns,
)

_SECOND_NS = 1_000_000_000


def _step(tag: str, start_s: int, end_s: int) -> Step:
    return Step(tag, start_s * _SECOND_NS, end_s * _SECOND_NS)


def _ledger_row(finish_reason: str, at_s: int) -> str:
    stamp = datetime.fromtimestamp(at_s, tz=UTC).isoformat()
    return json.dumps({"ts": stamp, "finish_reason": finish_reason, "call_index": 1})


def test_a_step_script_run_is_read_back_as_a_step(tmp_path: Path) -> None:
    script = tmp_path / "step.py"
    script.write_text(step_script(), encoding="utf-8")

    subprocess.run([sys.executable, str(script), "ALPHA"], check=True, cwd=tmp_path)

    [step] = read_steps(tmp_path / STEP_LOG_NAME)
    assert step.tag == "ALPHA"
    assert step.ended_ns - step.started_ns >= 400_000_000


def test_steps_are_listed_in_the_order_they_started(tmp_path: Path) -> None:
    log = tmp_path / STEP_LOG_NAME
    log.write_text("BRAVO start 20\nBRAVO end 30\nALPHA start 40\nALPHA end 50\n", encoding="utf-8")

    assert [step.tag for step in read_steps(log)] == ["BRAVO", "ALPHA"]


def test_a_step_that_has_not_ended_and_a_half_written_line_are_not_steps(tmp_path: Path) -> None:
    log = tmp_path / STEP_LOG_NAME
    log.write_text("ALPHA start 10\nALPHA end 20\nBRAVO start 30\nCHARLIE sta", encoding="utf-8")

    assert [step.tag for step in read_steps(log)] == ["ALPHA"]


def test_a_missing_log_has_no_steps(tmp_path: Path) -> None:
    assert read_steps(tmp_path / STEP_LOG_NAME) == []


def test_a_step_named_in_pauses_waits_its_own_time_and_the_others_keep_the_default(tmp_path: Path) -> None:
    script = tmp_path / "step.py"
    script.write_text(step_script({"ALPHA": 0}), encoding="utf-8")

    for tag in ("ALPHA", "BRAVO"):
        subprocess.run([sys.executable, str(script), tag], check=True, cwd=tmp_path)

    alpha, bravo = read_steps(tmp_path / STEP_LOG_NAME)
    assert alpha.ended_ns - alpha.started_ns < 400_000_000
    assert bravo.ended_ns - bravo.started_ns >= 400_000_000


def test_tags_that_started_are_listed_in_start_order_whether_or_not_they_ended(tmp_path: Path) -> None:
    log = tmp_path / STEP_LOG_NAME
    log.write_text("BRAVO start 10\nALPHA start 20\nALPHA end 30\nCHARLIE sta", encoding="utf-8")

    assert started_tags(log) == ["BRAVO", "ALPHA"]


def test_a_missing_log_has_no_started_tags(tmp_path: Path) -> None:
    assert started_tags(tmp_path / STEP_LOG_NAME) == []


def test_only_model_calls_that_ended_by_asking_for_tools_are_rounds(tmp_path: Path) -> None:
    ledger = tmp_path / "usage_ledger.jsonl"
    ledger.write_text(
        "\n".join(
            (_ledger_row("tool_calls", 100), _ledger_row("stop", 110), _ledger_row("tool_use", 120), '{"ts": "2026-11-13T07:2')
        ),
        encoding="utf-8",
    )

    assert tool_round_ends_ns(ledger) == [100 * _SECOND_NS, 120 * _SECOND_NS]


def test_a_missing_ledger_has_no_rounds(tmp_path: Path) -> None:
    assert tool_round_ends_ns(tmp_path / "usage_ledger.jsonl") == []


def test_steps_started_after_one_model_message_came_from_it() -> None:
    steps = [_step("ALPHA", 10, 11), _step("BRAVO", 13, 14), _step("CHARLIE", 18, 19)]

    assert ran_in_one_message(steps, [5 * _SECOND_NS])


def test_a_model_call_between_every_pair_of_starts_means_one_message_per_step() -> None:
    steps = [_step("ALPHA", 10, 11), _step("BRAVO", 20, 21), _step("CHARLIE", 30, 31)]

    assert not ran_in_one_message(steps, [5 * _SECOND_NS, 15 * _SECOND_NS, 25 * _SECOND_NS])


def test_one_pair_without_a_model_call_in_between_is_enough() -> None:
    steps = [_step("ALPHA", 10, 11), _step("BRAVO", 20, 21), _step("CHARLIE", 22, 23)]

    assert ran_in_one_message(steps, [5 * _SECOND_NS, 15 * _SECOND_NS])


def test_a_model_call_that_ended_as_the_later_step_started_lies_between() -> None:
    steps = [_step("ALPHA", 10, 11), _step("BRAVO", 20, 21)]

    assert not ran_in_one_message(steps, [20 * _SECOND_NS])


def test_a_model_call_that_ended_as_the_earlier_step_started_does_not_lie_between() -> None:
    steps = [_step("ALPHA", 10, 11), _step("BRAVO", 20, 21)]

    assert ran_in_one_message(steps, [10 * _SECOND_NS])


def test_fewer_than_two_steps_were_not_issued_together() -> None:
    assert not ran_in_one_message([_step("ALPHA", 10, 11)], [])
    assert not ran_in_one_message([], [])


def test_bash_step_tags_are_read_from_the_progress_steps_in_listed_order() -> None:
    bash_step = {"step_key": f"{BASH_STEP_KEY}_tool", "items": [{"code": "python3 /w/step.py BRAVO"}]}
    messages: list[object] = [
        {"role": "user", "content": "run them"},
        {
            "role": "assistant",
            "progressSteps": [
                {"step_key": f"{BASH_STEP_KEY}_tool", "items": [{"code": "python3 /w/step.py ALPHA"}]},
                {"step_key": "file_read_tool", "items": [{"code": "python3 /w/step.py ZULU"}]},
            ],
        },
        {"role": "assistant", "metadata": {"progressSteps": [bash_step, {"step_key": f"{BASH_STEP_KEY}_tool", "items": []}]}},
        "not a message",
    ]

    assert bash_tags_in(messages) == ["ALPHA", "BRAVO"]


def test_step_keys_name_every_stored_step_in_listed_order() -> None:
    messages: list[object] = [
        {"role": "user", "content": "write them"},
        {"role": "assistant", "progressSteps": [{"step_key": f"{BASH_STEP_KEY}_tool"}, {"tool_name": "file_write_tool"}]},
        {"role": "assistant", "metadata": {"progressSteps": [{"step_key": "file_write_tool_tool"}, "not a step", {"items": []}]}},
        "not a message",
    ]

    assert step_keys_in(messages) == [f"{BASH_STEP_KEY}_tool", "file_write_tool", "file_write_tool_tool", ""]


def test_the_batch_prompt_asks_for_the_shell_step_first_and_a_write_of_each_waiting_file_behind_it() -> None:
    lines = batch_prompt(Path("/w/step.py"), Path("/w")).splitlines()

    assert lines[1:] == [
        f"1. {BASH_STEP_KEY}: python3 /w/step.py {BATCH_SHELL_TAG}",
        f"2. {WRITE_STEP_KEY}: path /w/waiting_one.txt, content waiting_one.txt",
        f"3. {WRITE_STEP_KEY}: path /w/waiting_two.txt, content waiting_two.txt",
    ]


def test_the_stored_stop_reason_is_the_one_on_the_latest_assistant_message() -> None:
    messages: list[object] = [
        {"role": "assistant", "metadata": {"stopReason": {"detail": {"reason": "earlier"}}}},
        {"role": "assistant", "metadata": {"stopReason": {"detail": {"reason": "user_cancelled"}}}},
        {"role": "user", "content": "next"},
        "not a message",
    ]

    assert stored_stop_reason(messages) == "user_cancelled"


def test_a_chat_that_stored_no_stop_reason_has_none() -> None:
    assert stored_stop_reason([]) is None
    assert stored_stop_reason([{"role": "user", "content": "go"}]) is None
    assert stored_stop_reason([{"role": "assistant", "metadata": {"stopReason": {"detail": "not a mapping"}}}]) is None
