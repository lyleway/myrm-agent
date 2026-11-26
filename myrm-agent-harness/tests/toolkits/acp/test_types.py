"""Tests for the ACP runtime types: RuntimeConfig and RuntimeEvent."""

from __future__ import annotations

import pytest

from myrm_agent_harness.toolkits.acp.types import (
    AcpErrorCode,
    RuntimeConfig,
    RuntimeEventType,
    create_event,
    create_permission_request,
    delegation_session_id,
    normalize_workspace,
)


class TestRuntimeConfig:
    def test_defaults(self) -> None:
        cfg = RuntimeConfig(backend_type="acp", command="claude")
        assert cfg.command == "claude"
        assert cfg.args == []
        assert cfg.env is None
        assert cfg.timeout_seconds == 300
        assert cfg.permission_mode == "allow_all"
        assert cfg.max_response_chars == 50_000

    def test_custom_values(self) -> None:
        cfg = RuntimeConfig(
            backend_type="cli",
            command="codex",
            args=["--acp"],
            env={"MY_VAR": "1"},
            timeout_seconds=120,
            permission_mode="safe",
        )
        assert cfg.command == "codex"
        assert cfg.backend_type == "cli"
        assert cfg.timeout_seconds == 120
        assert cfg.permission_mode == "safe"

    def test_max_turns_default(self) -> None:
        cfg = RuntimeConfig(backend_type="acp", command="test")
        assert cfg.max_turns == 25

    def test_description_default(self) -> None:
        cfg = RuntimeConfig(backend_type="acp", command="test")
        assert cfg.description == ""

    def test_custom_max_turns_and_description(self) -> None:
        cfg = RuntimeConfig(
            backend_type="cli",
            command="claude",
            max_turns=50,
            description="A powerful coding agent",
        )
        assert cfg.max_turns == 50
        assert cfg.description == "A powerful coding agent"

    def test_max_turns_zero_disables(self) -> None:
        cfg = RuntimeConfig(backend_type="cli", command="claude", max_turns=0)
        assert cfg.max_turns == 0

    def test_frozen(self) -> None:
        cfg = RuntimeConfig(backend_type="acp", command="test")
        with pytest.raises(AttributeError):
            cfg.command = "other"  # type: ignore[misc]

    def test_a_config_carries_no_directory(self) -> None:
        # The directory a turn runs in belongs to the turn, so the same config can serve any number of workspaces.
        with pytest.raises(TypeError):
            RuntimeConfig(backend_type="cli", command="claude", cwd="/work")  # type: ignore[call-arg]


class TestNormalizeWorkspace:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("/work/repo", "/work/repo"),
            ("/work/repo/", "/work/repo"),
            ("/work//repo/./sub/..", "/work/repo"),
        ],
    )
    def test_equal_directories_get_one_spelling(self, given: str, expected: str) -> None:
        # The pool keys its backends by this spelling, so two spellings of one directory must not split them.
        assert normalize_workspace(given) == expected

    @pytest.mark.parametrize("relative", ["", ".", "repo", "../repo", "~/repo"])
    def test_relative_paths_are_rejected(self, relative: str) -> None:
        # A relative path would silently resolve against whatever directory the host process happens to be in.
        with pytest.raises(ValueError, match="absolute path"):
            normalize_workspace(relative)


class TestDelegationSessionId:
    def test_a_scope_names_the_session_of_that_scope(self) -> None:
        assert delegation_session_id("claude", "chat-1") == "claude-chat-1"

    @pytest.mark.parametrize("no_scope", [None, ""])
    def test_without_a_scope_the_agent_has_one_default_session(self, no_scope: str | None) -> None:
        assert delegation_session_id("claude", no_scope) == "claude-default"
        assert delegation_session_id("claude") == "claude-default"

    def test_agents_and_scopes_never_share_a_session(self) -> None:
        names = {delegation_session_id(agent, scope) for agent in ("claude", "codex") for scope in ("a", "b", None)}
        assert len(names) == 6


class TestRuntimeEvent:
    def test_create_event(self) -> None:
        event = create_event(RuntimeEventType.TEXT_DELTA, "sess-1", content="hello")
        assert event.type == RuntimeEventType.TEXT_DELTA
        assert event.data["content"] == "hello"
        assert event.session_id == "sess-1"
        assert event.timestamp > 0

    @pytest.mark.asyncio
    async def test_create_permission_request(self) -> None:
        event, _future = create_permission_request("sess-1", "Write", {"path": "/tmp"})
        assert event.type == RuntimeEventType.PERMISSION_REQUEST
        assert event.data["tool_name"] == "Write"
        assert "response_future" in event.data

    def test_event_types_count(self) -> None:
        assert len(RuntimeEventType) == 9

    def test_error_codes_count(self) -> None:
        assert len(AcpErrorCode) == 8
        assert AcpErrorCode.MAX_TURNS == "max_turns"
        assert AcpErrorCode.BACKEND_NOT_FOUND == "backend_not_found"
