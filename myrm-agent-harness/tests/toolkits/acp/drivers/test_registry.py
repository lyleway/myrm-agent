"""The registry of known agents: what each entry says, how a command finds its entry, and what keeps entries apart."""

from __future__ import annotations

from dataclasses import replace

import pytest

from myrm_agent_harness.toolkits.acp.drivers import (
    DriverSpec,
    LoginStrategy,
    all_drivers,
    cli_launch_args,
    command_driver,
    driver_for,
    effective_transport,
    known_backends,
    resolve_acp_args,
    resolve_app_server_args,
    resolve_cli_args,
)
from myrm_agent_harness.toolkits.acp.drivers.registry import _index


def _spec(backend: str) -> DriverSpec:
    spec = driver_for(backend)
    assert spec is not None
    return spec


class TestTheEntries:
    def test_the_agents_in_the_order_they_are_detected_and_listed(self) -> None:
        assert known_backends() == ("claude", "codex", "gemini", "qwen")
        assert tuple(spec.backend for spec in all_drivers()) == known_backends()

    def test_how_each_agent_is_driven(self) -> None:
        assert {spec.backend: spec.transport for spec in all_drivers()} == {
            "claude": "cli",
            "codex": "app_server",
            "gemini": "acp",
            "qwen": "acp",
        }

    def test_what_a_person_is_shown_of_each_agent(self) -> None:
        assert {spec.backend: (spec.title, spec.default_name, spec.description) for spec in all_drivers()} == {
            "claude": ("Claude Code", "claude-code", "Full-stack coding agent powered by Anthropic Claude"),
            "codex": (
                "Codex CLI",
                "codex-cli",
                "OpenAI Codex CLI coding agent (requires Responses API provider)",
            ),
            "gemini": ("Gemini CLI", "gemini-cli", "Google Gemini-powered coding agent"),
            "qwen": ("Qwen Code", "qwen-code", "Alibaba Qwen-powered coding agent"),
        }

    def test_each_agent_is_installed_from_its_official_package(self) -> None:
        assert {spec.backend: spec.npm_package for spec in all_drivers()} == {
            "claude": "@anthropic-ai/claude-code",
            "codex": "@openai/codex",
            "gemini": "@google/gemini-cli",
            "qwen": "@qwen-code/qwen-code",
        }

    def test_only_gemini_needs_a_trusted_folder_to_connect_to_an_mcp_server(self) -> None:
        assert {spec.backend: spec.mcp_needs_trusted_folder for spec in all_drivers()} == {
            "claude": False,
            "codex": False,
            "gemini": True,
            "qwen": False,
        }

    @pytest.mark.parametrize(
        ("backend", "strategy"),
        [
            ("codex", LoginStrategy.BROWSER_OAUTH),
            ("claude", LoginStrategy.TERMINAL),
            ("gemini", LoginStrategy.TERMINAL),
            ("qwen", LoginStrategy.TERMINAL),
        ],
    )
    def test_who_runs_the_login(self, backend: str, strategy: LoginStrategy) -> None:
        assert _spec(backend).login_strategy is strategy

    @pytest.mark.parametrize("spec", all_drivers(), ids=known_backends())
    def test_a_login_the_host_runs_has_a_command_to_run(self, spec: DriverSpec) -> None:
        if spec.login_strategy is LoginStrategy.BROWSER_OAUTH:
            assert spec.login_args

    def test_cli_launch_args_only_exist_for_an_agent_driven_as_a_cli_process(self) -> None:
        assert cli_launch_args("no-such-cli") == []
        assert cli_launch_args("claude") == ["-p", "--output-format", "stream-json", "--verbose"]
        for backend in ("gemini", "qwen", "codex"):
            assert cli_launch_args(backend) == []

    @pytest.mark.parametrize("spec", all_drivers(), ids=known_backends())
    def test_every_agent_starts_the_way_its_contract_says_in_every_mode_it_has(self, spec: DriverSpec) -> None:
        """An agent added with a transport but without what that transport needs resolves to nothing: caught here."""
        if spec.transport == "cli":
            assert spec.permission_modes
            for mode in spec.permission_modes:
                mode_args = spec.permission_args(mode)
                assert mode_args is not None
                assert resolve_cli_args(spec.backend, [], mode) == [*mode_args, *spec.launch_args]
        elif spec.transport == "acp":
            assert resolve_acp_args(spec.backend, []).args == list(spec.launch_args)
        else:
            assert resolve_app_server_args(spec.backend, [], "subscription").args == list(spec.launch_args)


class TestLookups:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("codex", "codex"),
            ("codex-cli", "codex"),
            ("claude-code", "claude"),
            ("/usr/local/bin/codex", "codex"),
            ("gemini.cmd", "gemini"),
            ("GEMINI.EXE", "gemini"),
            ("qwen", "qwen"),
        ],
    )
    def test_an_agent_is_found_by_its_name_or_by_a_name_that_carries_it(self, token: str, expected: str) -> None:
        assert _spec(token).backend == expected

    def test_a_name_that_carries_no_agent_finds_none(self) -> None:
        assert driver_for("totally-unknown-agent") is None
        assert driver_for("") is None

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("gemini", "gemini"),
            ("/usr/local/bin/gemini", "gemini"),
            ("gemini.cmd", "gemini"),
            ("GEMINI.EXE", "gemini"),
            ("claude", "claude"),
            # An adapter or wrapper that carries an agent's name is a command of its own.
            ("gemini-wrapper", None),
            ("claude-code-acp", None),
            ("my-qwen-wrapper", None),
            ("aider", None),
            ("", None),
            (None, None),
        ],
    )
    def test_a_command_is_a_known_agent_only_when_it_is_exactly_its_executable(
        self, command: str | None, expected: str | None
    ) -> None:
        found = command_driver(command)

        assert (found.backend if found is not None else None) == expected

    @pytest.mark.parametrize(
        ("command", "configured", "expected"),
        [
            ("gemini", "cli", "acp"),
            ("/usr/bin/claude", "acp", "cli"),
            ("codex.cmd", "cli", "app_server"),
            ("my-wrapper", "whatever", "whatever"),
            ("gemini-wrapper", "cli", "cli"),
            (None, "cli", "cli"),
        ],
    )
    def test_the_contract_of_a_known_agent_wins_over_the_transport_stored_for_it(
        self, command: str | None, configured: str, expected: str
    ) -> None:
        assert effective_transport(command, configured) == expected


class TestEntriesDoNotCollide:
    @pytest.mark.parametrize("spec", all_drivers(), ids=known_backends())
    def test_every_entry_is_found_by_its_backend_and_by_the_name_an_agent_added_from_it_starts_with(
        self, spec: DriverSpec
    ) -> None:
        assert command_driver(spec.backend) is spec
        assert driver_for(spec.default_name) is spec

    def test_no_two_agents_share_a_title_or_the_name_they_start_with(self) -> None:
        titles = [spec.title for spec in all_drivers()]
        names = [spec.default_name for spec in all_drivers()]

        assert len(set(titles)) == len(titles)
        assert len(set(names)) == len(names)

    def test_two_entries_of_one_backend_are_refused(self) -> None:
        claude = _spec("claude")

        with pytest.raises(ValueError, match="Two agents are registered as 'claude'"):
            _index([claude, replace(claude, default_name="claude-other")])

    def test_two_agents_added_under_one_name_are_refused(self) -> None:
        claude = _spec("claude")

        with pytest.raises(ValueError, match="'claude' and 'aider' are both added as 'claude-code'"):
            _index([claude, replace(claude, backend="aider")])

    def test_an_agent_whose_name_carries_another_backend_would_be_taken_for_it_and_is_refused(self) -> None:
        claude = _spec("claude")
        aider = replace(claude, backend="aider", title="Aider", default_name="claude-aider")

        with pytest.raises(ValueError, match="added as 'claude-aider' would be taken for another agent than 'aider'"):
            _index([claude, aider])

    def test_agents_that_do_not_collide_are_all_kept_in_order(self) -> None:
        claude = _spec("claude")
        aider = replace(claude, backend="aider", title="Aider", default_name="aider-cli")

        assert list(_index([claude, aider])) == ["claude", "aider"]
