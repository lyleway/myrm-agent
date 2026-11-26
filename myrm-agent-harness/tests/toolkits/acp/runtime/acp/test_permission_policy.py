"""How an ACP turn's permission requests are answered, with the SDK's own request and option objects."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from acp.schema import (
    AllowedOutcome,
    ContentToolCallContent,
    DeniedOutcome,
    FileEditToolCallContent,
    PermissionOption,
    TextContentBlock,
    ToolCallLocation,
    ToolCallUpdate,
)

from myrm_agent_harness.toolkits.acp.runtime.acp.permission_policy import AcpPermissionPolicy
from myrm_agent_harness.toolkits.acp.types import PermissionMode, PermissionRequest, PermissionVerdict

_ALL_OPTIONS = [
    PermissionOption(option_id="always-allow", name="Always", kind="allow_always"),
    PermissionOption(option_id="allow", name="Allow", kind="allow_once"),
    PermissionOption(option_id="always-reject", name="Never", kind="reject_always"),
    PermissionOption(option_id="reject", name="Reject", kind="reject_once"),
]


class _Broker:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.asked: list[PermissionRequest] = []

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked.append(request)
        return PermissionVerdict(allowed=self.allowed)


def _policy(workspace: Path, mode: PermissionMode = "safe", broker: _Broker | None = None) -> AcpPermissionPolicy:
    policy = AcpPermissionPolicy(mode, "gemini", str(workspace))
    policy.begin_turn(broker, "chat-1")
    return policy


def _call(kind: str | None = "edit", **fields: object) -> ToolCallUpdate:
    return ToolCallUpdate(tool_call_id="call-1", kind=kind, title="Do it", **fields)  # type: ignore[arg-type]


class TestTheAnswer:
    async def test_an_allowed_request_is_answered_with_the_agents_allow_once_option_even_when_always_comes_first(
        self, tmp_path: Path
    ) -> None:
        response = await _policy(tmp_path, broker=_Broker()).answer(_call(), _ALL_OPTIONS)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="allow")

    async def test_an_allowed_request_falls_back_to_allow_always_when_the_agent_offers_no_once(
        self, tmp_path: Path
    ) -> None:
        options = [_ALL_OPTIONS[0], _ALL_OPTIONS[3]]

        response = await _policy(tmp_path, broker=_Broker()).answer(_call(), options)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="always-allow")

    async def test_a_refused_request_is_answered_with_reject_once_before_reject_always(self, tmp_path: Path) -> None:
        response = await _policy(tmp_path, broker=_Broker(allowed=False)).answer(_call(), _ALL_OPTIONS)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="reject")

    async def test_an_agent_that_offers_no_option_of_the_wanted_kind_gets_a_cancel_and_the_log_says_why(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        only_allow = [_ALL_OPTIONS[1]]

        with caplog.at_level(logging.WARNING):
            response = await _policy(tmp_path, broker=_Broker(allowed=False)).answer(_call(), only_allow)

        assert response.outcome == DeniedOutcome(outcome="cancelled")
        assert any("acp_permission_no_matching_option" in record.message for record in caplog.records)

    async def test_the_outcome_keeps_its_discriminator_on_the_wire(self, tmp_path: Path) -> None:
        # The SDK serializes what a handler returns without defaults; the outcome would be unreadable without it.
        response = await _policy(tmp_path, broker=_Broker()).answer(_call(), _ALL_OPTIONS)

        dumped = response.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=True)

        assert dumped == {"outcome": {"outcome": "selected", "optionId": "allow"}}

    @pytest.mark.parametrize("kind", ["read", "search", "think"])
    async def test_what_only_reads_is_allowed_without_asking(self, tmp_path: Path, kind: str) -> None:
        broker = _Broker(allowed=False)

        response = await _policy(tmp_path, broker=broker).answer(_call(kind), _ALL_OPTIONS)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="allow")
        assert broker.asked == []

    @pytest.mark.parametrize("kind", ["edit", "delete", "move", "execute", "fetch", "switch_mode", "other", None])
    async def test_everything_else_is_asked_about(self, tmp_path: Path, kind: str | None) -> None:
        broker = _Broker()

        await _policy(tmp_path, broker=broker).answer(_call(kind), _ALL_OPTIONS)

        assert [request.kind for request in broker.asked] == [kind or "other"]

    async def test_allow_all_allows_everything_without_asking(self, tmp_path: Path) -> None:
        broker = _Broker(allowed=False)

        response = await _policy(tmp_path, "allow_all", broker).answer(_call("execute"), _ALL_OPTIONS)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="allow")
        assert broker.asked == []

    async def test_with_nobody_to_ask_a_request_is_refused_and_the_log_says_so(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO):
            response = await _policy(tmp_path).answer(_call(), _ALL_OPTIONS)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="reject")
        assert any("acp_permission_refused_unattended" in record.message for record in caplog.records)

    async def test_after_the_turn_a_late_request_is_refused(self, tmp_path: Path) -> None:
        broker = _Broker()
        policy = _policy(tmp_path, broker=broker)
        policy.end_turn()

        response = await policy.answer(_call(), _ALL_OPTIONS)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="reject")
        assert broker.asked == []


class TestTheRequestTheBrokerSees:
    async def test_it_carries_the_hosts_session_the_tool_and_its_input(self, tmp_path: Path) -> None:
        broker = _Broker()

        await _policy(tmp_path, broker=broker).answer(
            _call("execute", name="shell", raw_input={"command": "ls"}), _ALL_OPTIONS
        )

        (request,) = broker.asked
        assert (request.agent, request.session_id, request.tool_name, request.kind) == (
            "gemini",
            "chat-1",
            "shell",
            "execute",
        )
        assert request.title == "Do it"
        assert request.input == {"command": "ls"}
        assert request.tool_call_id == "call-1"

    @pytest.mark.parametrize(
        ("raw_input", "expected"),
        [(None, {}), ({"a": 1}, {"a": 1}), ("rm -rf x", {"input": "rm -rf x"}), (["a"], {"input": ["a"]})],
    )
    async def test_the_raw_input_is_always_a_mapping(self, tmp_path: Path, raw_input: object, expected: object) -> None:
        broker = _Broker()

        await _policy(tmp_path, broker=broker).answer(_call(raw_input=raw_input), _ALL_OPTIONS)

        assert broker.asked[0].input == expected

    async def test_the_tool_is_named_by_its_name_then_its_title_then_a_placeholder(self, tmp_path: Path) -> None:
        broker = _Broker()
        policy = _policy(tmp_path, broker=broker)

        await policy.answer(_call(name="shell"), _ALL_OPTIONS)
        await policy.answer(_call(), _ALL_OPTIONS)
        await policy.answer(ToolCallUpdate(tool_call_id="c", kind="edit"), _ALL_OPTIONS)

        assert [request.tool_name for request in broker.asked] == ["shell", "Do it", "tool"]

    async def test_the_files_come_from_locations_and_diffs_as_real_absolute_paths_without_repeats(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "real.txt").write_text("x")
        (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
        broker = _Broker()
        call = _call(
            locations=[ToolCallLocation(path="a.txt"), ToolCallLocation(path=str(tmp_path / "link.txt"))],
            content=[
                FileEditToolCallContent(type="diff", path="a.txt", new_text="n"),
                FileEditToolCallContent(type="diff", path="real.txt", new_text="n"),
                ContentToolCallContent(type="content", content=TextContentBlock(type="text", text="note")),
            ],
        )

        await _policy(tmp_path, broker=broker).answer(call, _ALL_OPTIONS)

        assert broker.asked[0].paths == (str((tmp_path / "a.txt").resolve()), str((tmp_path / "real.txt").resolve()))


class TestFileWrites:
    async def test_a_file_a_person_allowed_to_edit_may_then_be_written_without_asking_again(
        self, tmp_path: Path
    ) -> None:
        broker = _Broker()
        policy = _policy(tmp_path, broker=broker)
        await policy.answer(_call("edit", locations=[ToolCallLocation(path="a.txt")]), _ALL_OPTIONS)

        assert await policy.allows_write(str((tmp_path / "a.txt").resolve())) is True
        assert len(broker.asked) == 1

    @pytest.mark.parametrize("kind", ["delete", "move"])
    async def test_deleting_or_moving_a_file_allows_the_files_it_names_too(self, tmp_path: Path, kind: str) -> None:
        broker = _Broker()
        policy = _policy(tmp_path, broker=broker)
        await policy.answer(_call(kind, locations=[ToolCallLocation(path="a.txt")]), _ALL_OPTIONS)

        assert await policy.allows_write(str((tmp_path / "a.txt").resolve())) is True
        assert len(broker.asked) == 1

    async def test_running_a_command_does_not_allow_writing_the_files_it_mentions(self, tmp_path: Path) -> None:
        broker = _Broker()
        policy = _policy(tmp_path, broker=broker)
        await policy.answer(_call("execute", locations=[ToolCallLocation(path="a.txt")]), _ALL_OPTIONS)

        await policy.allows_write(str((tmp_path / "a.txt").resolve()))

        assert len(broker.asked) == 2

    async def test_a_refused_edit_allows_nothing(self, tmp_path: Path) -> None:
        broker = _Broker(allowed=False)
        policy = _policy(tmp_path, broker=broker)
        await policy.answer(_call("edit", locations=[ToolCallLocation(path="a.txt")]), _ALL_OPTIONS)

        assert await policy.allows_write(str((tmp_path / "a.txt").resolve())) is False

    async def test_a_write_nobody_was_asked_about_is_put_to_the_broker_as_an_edit_of_that_file(
        self, tmp_path: Path
    ) -> None:
        broker = _Broker()
        policy = _policy(tmp_path, broker=broker)
        path = str((tmp_path / "b.txt").resolve())

        assert await policy.allows_write(path) is True
        assert await policy.allows_write(path) is True

        (request,) = broker.asked  # allowed once, remembered for the turn
        assert (request.tool_name, request.kind, request.paths, request.session_id) == (
            "write_text_file",
            "edit",
            (path,),
            "chat-1",
        )

    async def test_a_refused_write_is_asked_about_again_next_time(self, tmp_path: Path) -> None:
        broker = _Broker(allowed=False)
        policy = _policy(tmp_path, broker=broker)
        path = str((tmp_path / "b.txt").resolve())

        assert await policy.allows_write(path) is False
        assert await policy.allows_write(path) is False

        assert len(broker.asked) == 2

    async def test_allow_all_writes_without_asking(self, tmp_path: Path) -> None:
        broker = _Broker(allowed=False)

        assert await _policy(tmp_path, "allow_all", broker).allows_write(str(tmp_path / "x")) is True
        assert broker.asked == []

    async def test_a_new_turn_starts_with_no_file_allowed(self, tmp_path: Path) -> None:
        broker = _Broker()
        policy = _policy(tmp_path, broker=broker)
        path = str((tmp_path / "b.txt").resolve())
        assert await policy.allows_write(path) is True

        policy.begin_turn(_Broker(allowed=False), "chat-2")

        assert await policy.allows_write(path) is False
