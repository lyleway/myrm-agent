"""The small pure parts of the Codex driver: the typed reads, failure classification, usage steps and the item book."""

from __future__ import annotations

import pytest

from myrm_agent_harness.toolkits.acp.runtime.codex.failure import classify_turn_error, is_auth_rejection
from myrm_agent_harness.toolkits.acp.runtime.codex.items import ItemBook, changes_summary, file_changes
from myrm_agent_harness.toolkits.acp.runtime.codex.rpc import Notification
from myrm_agent_harness.toolkits.acp.runtime.codex.usage import TokenCounts, UsageMeter
from myrm_agent_harness.toolkits.acp.runtime.codex.wire import as_bool, as_int, as_list, as_obj, as_str
from myrm_agent_harness.toolkits.acp.types import AcpErrorCode

# -- the typed reads ---------------------------------------------------------------------------------------------------------


def test_every_read_degrades_to_an_empty_value_for_the_wrong_type() -> None:
    wrong_values: tuple[object, ...] = (None, 1.5, [], {}, "x", True)
    for wrong in wrong_values:
        if not isinstance(wrong, dict):
            assert as_obj(wrong) == {}
        if not isinstance(wrong, str):
            assert as_str(wrong) == ""
        if not isinstance(wrong, list):
            assert as_list(wrong) == []

    assert (as_int(True), as_int("3"), as_int(None), as_int(7)) == (0, 0, 0, 7)
    assert (as_bool(True), as_bool(1), as_bool("true"), as_bool(None)) == (True, False, False, False)


# -- failures --------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "code", "retryable"),
    [
        ("unauthorized", AcpErrorCode.AUTH_FAILED, False),
        ({"responseStreamDisconnected": {"httpStatusCode": 401}}, AcpErrorCode.AUTH_FAILED, False),
        ("rateLimitExceeded", AcpErrorCode.RATE_LIMITED, True),
        ({"httpConnectionFailed": {"httpStatusCode": 429}}, AcpErrorCode.RATE_LIMITED, True),
        ("serverOverloaded", AcpErrorCode.UNKNOWN, True),
        ("internalServerError", AcpErrorCode.UNKNOWN, True),
        ({"httpConnectionFailed": {"httpStatusCode": 503}}, AcpErrorCode.UNKNOWN, True),
        ({"httpConnectionFailed": {"httpStatusCode": None}}, AcpErrorCode.UNKNOWN, False),
        ("badRequest", AcpErrorCode.UNKNOWN, False),
        ("usageLimitExceeded", AcpErrorCode.UNKNOWN, False),
        ({}, AcpErrorCode.UNKNOWN, False),
        (None, AcpErrorCode.UNKNOWN, False),
    ],
)
def test_the_category_the_server_names_decides_the_error_code(
    info: object, code: AcpErrorCode, retryable: bool
) -> None:
    error = classify_turn_error({"message": "failed", "codexErrorInfo": info})

    assert (error.code, error.retryable) == (code, retryable)
    assert error.message == "failed"


def test_a_403_is_not_a_refused_login_because_a_blocked_region_answers_it_too() -> None:
    assert not is_auth_rejection({"codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 403}}})
    assert is_auth_rejection({"codexErrorInfo": "unauthorized"})


def test_the_message_is_the_servers_own_and_is_kept_short() -> None:
    assert classify_turn_error({}).message == "The turn failed"
    assert len(classify_turn_error({"message": "x" * 5000}).message) <= 600


# -- usage ---------------------------------------------------------------------------------------------------------------------


def _counts(value: int) -> TokenCounts:
    return TokenCounts(input=value, output=value // 2, cached=value // 5)


def test_the_first_report_costs_only_its_last_request_whatever_the_thread_already_held() -> None:
    meter = UsageMeter()

    assert meter.step(_counts(9000), _counts(100)) == _counts(100)


def test_each_later_report_costs_what_it_added_and_a_repeat_costs_nothing() -> None:
    meter = UsageMeter()
    meter.step(_counts(100), _counts(100))

    assert meter.step(_counts(250), _counts(150)) == _counts(150)
    assert meter.step(_counts(250), _counts(150)).empty


def test_totals_that_restart_never_cost_a_negative_amount() -> None:
    meter = UsageMeter()
    meter.step(_counts(500), _counts(500))

    assert meter.step(_counts(50), _counts(50)) == TokenCounts()


def test_the_counters_of_a_breakdown_are_read_defensively() -> None:
    counts = TokenCounts.of(
        {"inputTokens": 7, "outputTokens": "x", "cachedInputTokens": True, "cacheWriteInputTokens": 3}
    )

    assert counts == TokenCounts(input=7, output=0, cached=0, cache_write=3)


# -- the items -----------------------------------------------------------------------------------------------------------------


def test_the_changes_of_a_patch_are_read_with_their_kind_and_rename() -> None:
    item = {
        "changes": [
            {"path": "a.py", "kind": {"type": "update", "move_path": "b.py"}, "diff": "@@"},
            {"path": "c.py", "kind": {"type": "add"}, "diff": "+x"},
            {"path": "d.py", "kind": {"type": "delete"}},
            {"path": "e.py"},
            "junk",
        ]
    }

    changes = file_changes(item)

    assert [(c.kind, c.path, c.moved_to) for c in changes] == [
        ("update", "a.py", "b.py"),
        ("add", "c.py", ""),
        ("delete", "d.py", ""),
        ("update", "e.py", ""),
        ("update", "", ""),
    ]


def test_a_patch_summary_is_one_line_cut_to_a_readable_length() -> None:
    long = file_changes({"changes": [{"path": f"dir/file_{i}.py", "kind": {"type": "add"}} for i in range(30)]})

    assert changes_summary(file_changes({"changes": [{"path": "a.py", "kind": {"type": "add"}}]})) == "add a.py"
    assert len(changes_summary(long)) == 80
    assert changes_summary(long).endswith("…")


def _announce(book: ItemBook, method: str, turn: str, item: dict[str, object]) -> None:
    book.observe(Notification(method, {"turnId": turn, "item": item}))


def test_the_item_book_keeps_the_latest_version_of_each_item_by_turn() -> None:
    book = ItemBook()

    _announce(book, "item/started", "t1", {"id": "i", "status": "inProgress"})
    _announce(book, "item/completed", "t1", {"id": "i", "status": "completed"})
    _announce(book, "item/started", "t2", {"id": "i", "status": "inProgress"})

    assert book.get("t1", "i")["status"] == "completed"
    assert book.get("t2", "i")["status"] == "inProgress"
    assert book.get("t1", "missing") == {}


def test_the_item_book_ignores_other_notifications_and_items_without_an_id_and_can_be_cleared() -> None:
    book = ItemBook()

    book.observe(Notification("item/agentMessage/delta", {"turnId": "t", "item": {"id": "i"}}))
    _announce(book, "item/started", "t", {"status": "x"})
    _announce(book, "item/started", "t", {"id": "kept"})
    book.clear()

    assert book.get("t", "i") == book.get("t", "kept") == {}


def test_the_item_book_is_bounded_and_forgets_the_oldest_first() -> None:
    book = ItemBook()

    for number in range(600):
        _announce(book, "item/started", "t", {"id": f"i{number}"})

    assert book.get("t", "i0") == {}
    assert book.get("t", "i87") == {}
    assert book.get("t", "i88") == {"id": "i88"}
    assert book.get("t", "i599") == {"id": "i599"}
