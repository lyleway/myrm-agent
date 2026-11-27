"""How the notifications of one Codex turn become the events a host shows."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from myrm_agent_harness.toolkits.acp.runtime._base import UNAUTHORIZED_RETRIES_LIMIT
from myrm_agent_harness.toolkits.acp.runtime.codex.rpc import Notification
from myrm_agent_harness.toolkits.acp.runtime.codex.turn_events import TurnTranslator
from myrm_agent_harness.toolkits.acp.runtime.codex.usage import UsageMeter
from myrm_agent_harness.toolkits.acp.types import RuntimeEvent, RuntimeEventType

_THREAD, _TURN = "thread-1", "turn-1"
_UNAUTHORIZED = {
    "message": "unexpected status 401",
    "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 401}},
}


def _translator() -> TurnTranslator:
    return TurnTranslator("chat-1", _THREAD, _TURN, UsageMeter())


def _feed(translator: TurnTranslator, method: str, **params: object) -> list[RuntimeEvent]:
    return translator.feed(Notification(method, {"threadId": _THREAD, "turnId": _TURN, **params}))


def _kinds(events: list[RuntimeEvent]) -> list[RuntimeEventType]:
    return [event.type for event in events]


def _item(kind: str, item_id: str = "i-1", **fields: object) -> Mapping[str, object]:
    return {"type": kind, "id": item_id, **fields}


def _text(events: list[RuntimeEvent]) -> str:
    return "".join(str(e.data["content"]) for e in events if e.type is RuntimeEventType.TEXT_DELTA)


# -- text ----------------------------------------------------------------------------------------------------------------------


def test_the_text_of_a_message_streams_as_it_arrives() -> None:
    translator = _translator()

    events = [*_feed(translator, "item/agentMessage/delta", itemId="m", delta="hel")]
    events += _feed(translator, "item/agentMessage/delta", itemId="m", delta="lo")

    assert _kinds(events) == [RuntimeEventType.TEXT_DELTA] * 2
    assert _text(events) == "hello"


def test_a_message_that_never_streamed_is_told_when_it_completes() -> None:
    events = _feed(_translator(), "item/completed", item=_item("agentMessage", "a", text="whole"))

    assert _text(events) == "whole"


def test_a_message_that_streamed_is_not_told_again_when_it_completes() -> None:
    translator = _translator()

    _feed(translator, "item/agentMessage/delta", itemId="a", delta="one")

    assert _feed(translator, "item/completed", item=_item("agentMessage", "a", text="one")) == []


def test_a_message_after_earlier_text_is_set_apart_by_a_blank_line() -> None:
    translator = _translator()

    first = _feed(translator, "item/agentMessage/delta", itemId="a", delta="before the tool")
    _feed(translator, "item/started", item=_item("agentMessage", "b"))
    second = _feed(translator, "item/agentMessage/delta", itemId="b", delta="after")
    third = _feed(translator, "item/agentMessage/delta", itemId="b", delta=" it")

    assert _text(first) == "before the tool"
    assert _text(second) == "\n\nafter"
    assert _text(third) == " it"


def test_the_first_message_of_a_turn_has_no_blank_line_in_front() -> None:
    translator = _translator()

    _feed(translator, "item/started", item=_item("agentMessage", "a"))

    assert _text(_feed(translator, "item/agentMessage/delta", itemId="a", delta="hi")) == "hi"


def test_reasoning_streams_in_sections_set_apart_and_is_not_repeated_when_the_item_completes() -> None:
    translator = _translator()

    one = _feed(translator, "item/reasoning/summaryTextDelta", itemId="r", delta="think")
    _feed(translator, "item/reasoning/summaryPartAdded", itemId="r")
    two = _feed(translator, "item/reasoning/summaryTextDelta", itemId="r", delta="more")
    done = _feed(translator, "item/completed", item=_item("reasoning", "r", summary=["think", "more"]))

    assert [e.data["content"] for e in one + two] == ["think", "\n\nmore"]
    assert done == []


def test_reasoning_that_never_streamed_is_told_when_it_completes() -> None:
    events = _feed(_translator(), "item/completed", item=_item("reasoning", "r", summary=["a", "", "b"]))

    assert [(e.type, e.data["content"]) for e in events] == [(RuntimeEventType.REASONING_DELTA, "a\n\nb")]


# -- tools -----------------------------------------------------------------------------------------------------------------------


def test_a_command_is_a_tool_call() -> None:
    (start,) = _feed(_translator(), "item/started", item=_item("commandExecution", "c", command="ls -l"))

    assert (start.type, start.data["tool_name"], start.data["tool_input"]) == (
        RuntimeEventType.TOOL_START,
        "command_execution",
        {"command": "ls -l"},
    )
    assert start.data["tool_call_id"] == "c"
    assert start.data["summary"]


@pytest.mark.parametrize(
    ("status", "exit_code", "is_error"),
    [("completed", 0, False), ("completed", 2, True), ("failed", 1, True), ("declined", None, True)],
)
def test_the_result_of_a_command_is_an_error_when_it_failed_exited_non_zero_or_was_refused(
    status: str, exit_code: int | None, is_error: bool
) -> None:
    item = _item("commandExecution", "c", status=status, aggregatedOutput="out", exitCode=exit_code)

    (result,) = _feed(_translator(), "item/completed", item=item)

    assert (result.type, result.data["tool_call_id"], result.data["output"]) == (
        RuntimeEventType.TOOL_RESULT,
        "c",
        "out",
    )
    assert result.data["is_error"] is is_error


def test_a_tool_output_is_cut_to_the_length_hosts_accept() -> None:
    (result,) = _feed(
        _translator(),
        "item/completed",
        item=_item("commandExecution", "c", status="completed", aggregatedOutput="x" * 5000, exitCode=0),
    )

    assert len(str(result.data["output"])) <= 2000


def test_a_patch_is_a_tool_call_that_names_the_files_it_touches() -> None:
    translator = _translator()
    changes = [{"path": "a.py", "kind": {"type": "update"}, "diff": "@@"}, {"path": "b.py", "kind": {"type": "add"}}]

    (start,) = _feed(translator, "item/started", item=_item("fileChange", "f", changes=changes))
    (done,) = _feed(translator, "item/completed", item=_item("fileChange", "f", status="completed", changes=changes))
    (refused,) = _feed(translator, "item/completed", item=_item("fileChange", "f", status="declined", changes=changes))

    assert start.data["tool_name"] == "file_change"
    assert start.data["tool_input"] == {
        "changes": [{"kind": "update", "path": "a.py"}, {"kind": "add", "path": "b.py"}]
    }
    assert start.data["summary"] == "update a.py, add b.py"
    assert (done.data["output"], done.data["is_error"]) == ("update a.py, add b.py", False)
    assert refused.data["is_error"] is True


def test_an_mcp_tool_call_is_named_by_its_server_and_tool_and_its_failure_is_an_error() -> None:
    translator = _translator()
    arguments = {"text": "hi"}

    (start,) = _feed(
        translator, "item/started", item=_item("mcpToolCall", "m", server="echo", tool="ping", arguments=arguments)
    )
    (ok,) = _feed(
        translator,
        "item/completed",
        item=_item(
            "mcpToolCall",
            "m",
            status="completed",
            result={"content": [{"type": "text", "text": "pong:"}, {"text": "hi"}]},
        ),
    )
    (failed,) = _feed(
        translator, "item/completed", item=_item("mcpToolCall", "m", status="failed", error={"message": "no such tool"})
    )

    assert (start.data["tool_name"], start.data["tool_input"]) == ("mcp__echo__ping", arguments)
    assert (ok.data["output"], ok.data["is_error"]) == ("pong:hi", False)
    assert (failed.data["output"], failed.data["is_error"]) == ("no such tool", True)


def test_a_web_search_and_a_dynamic_tool_are_tool_calls() -> None:
    translator = _translator()

    (search,) = _feed(translator, "item/started", item=_item("webSearch", "w", query="codex"))
    (dynamic,) = _feed(
        translator, "item/started", item=_item("dynamicToolCall", "d", tool="lookup", arguments={"k": 1})
    )
    (dynamic_failed,) = _feed(translator, "item/completed", item=_item("dynamicToolCall", "d", success=False))
    (dynamic_ok,) = _feed(translator, "item/completed", item=_item("dynamicToolCall", "d", success=True))

    assert (search.data["tool_name"], search.data["tool_input"]) == ("web_search", {"query": "codex"})
    assert (dynamic.data["tool_name"], dynamic.data["tool_input"]) == ("lookup", {"k": 1})
    assert (dynamic_failed.data["is_error"], dynamic_ok.data["is_error"]) == (True, False)


@pytest.mark.parametrize("kind", ["userMessage", "plan", "contextCompaction", "somethingNew"])
def test_items_the_host_does_not_show_stay_silent(kind: str) -> None:
    translator = _translator()

    assert _feed(translator, "item/started", item=_item(kind)) == []
    assert _feed(translator, "item/completed", item=_item(kind)) == []


# -- usage, notices, the end ---------------------------------------------------------------------------------------------------


def _usage(total_in: int, total_out: int, last_in: int, last_out: int) -> dict[str, object]:
    def breakdown(i: int, o: int) -> dict[str, int]:
        return {
            "inputTokens": i,
            "outputTokens": o,
            "cachedInputTokens": 0,
            "cacheWriteInputTokens": 0,
            "totalTokens": i + o,
        }

    return {"total": breakdown(total_in, total_out), "last": breakdown(last_in, last_out)}


def test_usage_is_reported_as_what_each_report_adds() -> None:
    translator = _translator()

    first = _feed(translator, "thread/tokenUsage/updated", tokenUsage=_usage(10, 5, 10, 5))
    repeated = _feed(translator, "thread/tokenUsage/updated", tokenUsage=_usage(10, 5, 10, 5))
    second = _feed(translator, "thread/tokenUsage/updated", tokenUsage=_usage(25, 12, 15, 7))

    assert [(e.data["input_tokens"], e.data["output_tokens"]) for e in first] == [(10, 5)]
    assert repeated == []
    assert [(e.data["input_tokens"], e.data["output_tokens"]) for e in second] == [(15, 7)]


def test_a_thread_resumed_with_a_history_bills_the_turn_only_for_its_own_request() -> None:
    events = _feed(_translator(), "thread/tokenUsage/updated", tokenUsage=_usage(9000, 800, 120, 30))

    assert [(e.data["input_tokens"], e.data["output_tokens"]) for e in events] == [(120, 30)]


def test_a_request_the_server_retries_is_a_warning_and_the_turn_goes_on() -> None:
    translator = _translator()

    events = _feed(translator, "error", error={"message": "stream disconnected, retrying 2/5"}, willRetry=True)

    assert [(e.type, e.data["status"], e.data["message"]) for e in events] == [
        (RuntimeEventType.STATUS_UPDATE, "warning", "stream disconnected, retrying 2/5")
    ]
    assert translator.outcome is None
    assert translator.auth_rejection == ""


def test_a_refused_login_that_keeps_being_retried_is_given_up_and_a_sign_of_life_resets_the_count() -> None:
    translator = _translator()

    for _ in range(UNAUTHORIZED_RETRIES_LIMIT - 1):
        _feed(translator, "error", error=_UNAUTHORIZED, willRetry=True)
    _feed(translator, "item/agentMessage/delta", itemId="m", delta="alive")
    for _ in range(UNAUTHORIZED_RETRIES_LIMIT - 1):
        _feed(translator, "error", error=_UNAUTHORIZED, willRetry=True)
    assert translator.auth_rejection == ""

    _feed(translator, "error", error=_UNAUTHORIZED, willRetry=True)

    assert translator.auth_rejection == "unexpected status 401"


def test_other_retried_failures_do_not_count_as_a_refused_login() -> None:
    translator = _translator()

    for _ in range(UNAUTHORIZED_RETRIES_LIMIT * 2):
        _feed(
            translator, "error", error={"message": "overloaded", "codexErrorInfo": "serverOverloaded"}, willRetry=True
        )

    assert translator.auth_rejection == ""


def test_warnings_and_failed_mcp_servers_are_status_notices_and_the_rest_is_silent() -> None:
    translator = _translator()

    warned = _feed(translator, "warning", message="Model metadata not found")
    failed = _feed(translator, "mcpServer/startupStatus/updated", name="echo", status="failed", error="spawn error")
    nameless = _feed(translator, "mcpServer/startupStatus/updated", name="echo", status="failed")
    started = _feed(translator, "mcpServer/startupStatus/updated", name="echo", status="ready")
    empty = _feed(translator, "warning", message="  ")

    assert [e.data["message"] for e in warned] == ["Model metadata not found"]
    assert [e.data["message"] for e in failed] == ["echo: spawn error"]
    assert [e.data["message"] for e in nameless] == ["echo"]
    assert started == empty == []


def test_a_turn_ends_with_the_status_and_the_error_the_server_reports() -> None:
    translator = _translator()
    failure = {"message": "boom", "codexErrorInfo": "internalServerError"}

    assert _feed(translator, "turn/completed", turn={"id": _TURN, "status": "failed", "error": failure}) == []

    assert translator.outcome is not None
    assert (translator.outcome.status, translator.outcome.error) == ("failed", failure)


def test_a_failed_turn_without_an_error_of_its_own_carries_the_last_one_the_server_did_not_retry() -> None:
    translator = _translator()
    last = {"message": "the model refused"}
    _feed(translator, "error", error=last, willRetry=False)

    _feed(translator, "turn/completed", turn={"id": _TURN, "status": "failed"})

    assert translator.outcome is not None
    assert translator.outcome.error == last


def test_what_belongs_to_another_turn_or_thread_is_ignored() -> None:
    translator = _translator()

    other_turn = translator.feed(
        Notification("item/agentMessage/delta", {"threadId": _THREAD, "turnId": "t-2", "itemId": "m", "delta": "x"})
    )
    other_thread = translator.feed(
        Notification("item/agentMessage/delta", {"threadId": "other", "turnId": _TURN, "itemId": "m", "delta": "x"})
    )
    translator.feed(Notification("turn/completed", {"threadId": _THREAD, "turn": {"id": "t-2", "status": "failed"}}))

    assert other_turn == other_thread == []
    assert translator.outcome is None


def test_an_unknown_notification_and_a_malformed_one_are_ignored() -> None:
    translator = _translator()

    assert _feed(translator, "thread/somethingNew", x=1) == []
    assert _feed(translator, "item/started", item="not an object") == []
    assert _feed(translator, "item/agentMessage/delta", delta=None) == []
    assert _feed(translator, "thread/tokenUsage/updated", tokenUsage=None) == []
    assert translator.outcome is None
