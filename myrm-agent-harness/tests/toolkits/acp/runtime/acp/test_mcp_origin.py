"""A tool of an MCP server the host declared read-only runs without asking; any other tool is asked about as before."""

from __future__ import annotations

from pathlib import Path

import pytest
from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption, ToolCallUpdate

from myrm_agent_harness.toolkits.acp.runtime.acp.mcp_origin import mcp_server_of
from myrm_agent_harness.toolkits.acp.runtime.acp.permission_policy import AcpPermissionPolicy
from myrm_agent_harness.toolkits.acp.types import PermissionMode, PermissionRequest, PermissionVerdict

_OPTIONS = [
    PermissionOption(option_id="allow", name="Allow", kind="allow_once"),
    PermissionOption(option_id="reject", name="Reject", kind="reject_once"),
]
_SERVERS = {"memory", "my-notes"}


def _gemini(server: str) -> dict[str, object]:
    """The ``_meta`` a real Gemini 0.71 puts on the permission request of an MCP tool call."""
    return {"gemini-cli": {"mcp": {"server_name": server, "tool_name": "lookup"}}}


def _qwen(tool: str) -> dict[str, object]:
    """The ``_meta`` a real Qwen 0.28 puts on the permission request of any tool call."""
    return {"toolName": tool}


def _call(meta: dict[str, object] | None, kind: str = "other") -> ToolCallUpdate:
    return ToolCallUpdate(tool_call_id="call-1", kind=kind, title="lookup", field_meta=meta)  # type: ignore[arg-type]


class TestWhichServerACallBelongsTo:
    def test_gemini_names_the_server(self) -> None:
        assert mcp_server_of(_call(_gemini("memory")), _SERVERS) == "memory"

    def test_qwen_names_the_server_in_the_tool_name(self) -> None:
        assert mcp_server_of(_call(_qwen("mcp__my-notes__lookup")), _SERVERS) == "my-notes"

    def test_a_tool_name_that_holds_the_separator_still_belongs_to_the_server_it_starts_with(self) -> None:
        assert mcp_server_of(_call(_qwen("mcp__memory__recall__recent")), _SERVERS) == "memory"

    def test_a_server_the_host_did_not_hand_over_is_nobodys(self) -> None:
        assert mcp_server_of(_call(_gemini("elsewhere")), _SERVERS) is None
        assert mcp_server_of(_call(_qwen("mcp__elsewhere__lookup")), _SERVERS) is None

    def test_a_server_whose_name_only_starts_like_one_of_ours_is_nobodys(self) -> None:
        assert mcp_server_of(_call(_qwen("mcp__memory-extra__lookup")), _SERVERS) is None
        assert mcp_server_of(_call(_qwen("mcp__memorylookup")), _SERVERS) is None

    def test_what_an_agent_says_in_the_gemini_form_is_not_overruled_by_a_tool_name(self) -> None:
        assert mcp_server_of(_call({**_gemini("elsewhere"), **_qwen("mcp__memory__lookup")}), _SERVERS) is None

    @pytest.mark.parametrize(
        "meta",
        [
            None,
            {},
            {"toolName": "read_file"},
            {"toolName": 7},
            {"gemini-cli": {}},
            {"gemini-cli": {"mcp": "memory"}},
            {"gemini-cli": {"mcp": {"server_name": 7}}},
            {"gemini-cli": {"mcp": {"tool_name": "lookup"}}},
            {"other-agent": {"mcp": {"server_name": "memory"}}},
        ],
    )
    def test_a_call_that_names_no_server_has_no_origin(self, meta: dict[str, object] | None) -> None:
        assert mcp_server_of(_call(meta), _SERVERS) is None

    def test_a_call_of_the_agents_own_tool_is_never_taken_for_a_server_tool_even_when_the_agent_says_gemini(
        self,
    ) -> None:
        assert mcp_server_of(_call({"gemini-cli": {"toolName": "memory"}}), _SERVERS) is None

    def test_no_declared_server_means_no_origin(self) -> None:
        assert mcp_server_of(_call(_gemini("memory")), ()) is None
        assert mcp_server_of(_call(_qwen("mcp__memory__lookup")), ()) is None


class _Broker:
    def __init__(self, allowed: bool = False) -> None:
        self._allowed = allowed
        self.asked: list[PermissionRequest] = []

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked.append(request)
        return PermissionVerdict(allowed=self._allowed)


def _policy(
    workspace: Path, broker: _Broker | None, servers: set[str], mode: PermissionMode = "safe"
) -> AcpPermissionPolicy:
    policy = AcpPermissionPolicy(mode, "gemini", str(workspace))
    policy.begin_turn(broker, "chat-1", servers)
    return policy


class TestThePolicy:
    @pytest.mark.parametrize("meta", [_gemini("memory"), _qwen("mcp__memory__lookup")])
    async def test_a_tool_of_a_read_only_server_is_allowed_and_nobody_is_asked(
        self, tmp_path: Path, meta: dict[str, object]
    ) -> None:
        broker = _Broker()

        response = await _policy(tmp_path, broker, {"memory"}).answer(_call(meta), _OPTIONS)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="allow")
        assert broker.asked == []

    async def test_it_is_allowed_with_nobody_to_ask_too(self, tmp_path: Path) -> None:
        response = await _policy(tmp_path, None, {"memory"}).answer(_call(_gemini("memory")), _OPTIONS)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="allow")

    async def test_a_tool_of_any_other_server_is_put_to_the_broker(self, tmp_path: Path) -> None:
        broker = _Broker()

        response = await _policy(tmp_path, broker, {"memory"}).answer(_call(_gemini("elsewhere")), _OPTIONS)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="reject")
        assert len(broker.asked) == 1

    async def test_the_same_tool_is_asked_about_when_the_host_did_not_declare_its_server_read_only(
        self, tmp_path: Path
    ) -> None:
        broker = _Broker()

        await _policy(tmp_path, broker, set()).answer(_call(_gemini("memory")), _OPTIONS)

        assert len(broker.asked) == 1

    async def test_a_new_turn_forgets_the_servers_of_the_last_one(self, tmp_path: Path) -> None:
        broker = _Broker()
        policy = _policy(tmp_path, broker, {"memory"})
        policy.begin_turn(broker, "chat-1")

        await policy.answer(_call(_gemini("memory")), _OPTIONS)

        assert len(broker.asked) == 1

    async def test_a_turn_that_ended_asks_nobody_and_refuses(self, tmp_path: Path) -> None:
        policy = _policy(tmp_path, _Broker(allowed=True), {"memory"})
        policy.end_turn()

        response = await policy.answer(_call(_gemini("elsewhere")), _OPTIONS)

        assert response.outcome == AllowedOutcome(outcome="selected", option_id="reject")

    async def test_an_agent_that_offers_no_allow_option_gets_a_cancel_for_a_server_tool_too(
        self, tmp_path: Path
    ) -> None:
        response = await _policy(tmp_path, None, {"memory"}).answer(_call(_gemini("memory")), [_OPTIONS[1]])

        assert response.outcome == DeniedOutcome(outcome="cancelled")
