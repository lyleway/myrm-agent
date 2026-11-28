"""A DriverSpec that contradicts itself cannot be built, and what a spec derives from its facts.

Each rule is shown by building the real spec of a known agent with one fact changed: the spec is rejected for that
fact, and for no other.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.drivers import DriverSpec, PermissionFlags, driver_for

_APPROVAL_FLAGS = PermissionFlags(
    names=frozenset({"-y", "--yolo", "--approval-mode"}), value_names=frozenset({"--approval-mode"})
)


def _spec(backend: str) -> DriverSpec:
    spec = driver_for(backend)
    assert spec is not None
    return spec


class TestWhatACliAgentIs:
    @pytest.mark.parametrize("backend", ["Claude", "claude.cmd", "/usr/bin/claude", "", " ", "cl aude", "-claude"])
    def test_a_backend_is_the_bare_lowercase_name_of_its_executable(self, backend: str) -> None:
        with pytest.raises(ValueError, match="bare lowercase name of its executable"):
            replace(_spec("claude"), backend=backend)

    @pytest.mark.parametrize("field", ["title", "default_name", "description", "npm_package"])
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_what_a_person_and_the_installer_need_to_know_is_never_blank(self, field: str, blank: str) -> None:
        with pytest.raises(ValueError, match=f"{field} must not be blank"):
            replace(_spec("claude"), **{field: blank})

    def test_every_problem_is_reported_at_once_with_the_agent_it_is_in(self) -> None:
        with pytest.raises(ValueError, match="'claude' contradicts itself") as raised:
            replace(_spec("claude"), title="", npm_package="", permission_flags=None)

        assert "title, npm_package must not be blank" in str(raised.value)
        assert "needs permission_flags" in str(raised.value)

    def test_a_cli_agent_maps_every_permission_mode_exactly_once(self) -> None:
        claude = _spec("claude")
        safe_only = claude.permission_mode_args[:1]

        for mapping in (safe_only, (), (*claude.permission_mode_args, *safe_only)):
            with pytest.raises(ValueError, match="maps every permission mode exactly once"):
                replace(claude, permission_mode_args=mapping)

    @pytest.mark.parametrize("args", [(), ("--something-else", "x")])
    def test_a_mode_starts_with_a_flag_the_host_owns_or_a_configured_one_would_repeat_it(
        self, args: tuple[str, ...]
    ) -> None:
        claude = _spec("claude")
        other_modes = tuple(mode for mode in claude.permission_mode_args if mode[0] != "safe")

        with pytest.raises(ValueError, match="mode 'safe' must start with a flag of permission_flags"):
            replace(claude, permission_mode_args=(("safe", args), *other_modes))

    def test_a_cli_agent_may_have_no_baseline_launch_args(self) -> None:
        assert replace(_spec("claude"), launch_args=()).launch_args == ()


class TestWhatAnAgentThatAsksTheHostIs:
    @pytest.mark.parametrize("backend", ["claude", "gemini"])
    def test_an_agent_the_host_launches_by_flags_needs_the_flags_it_owns(self, backend: str) -> None:
        with pytest.raises(ValueError, match="needs permission_flags"):
            replace(_spec(backend), permission_flags=None)

    def test_an_app_server_agent_has_no_permission_flags(self) -> None:
        with pytest.raises(ValueError, match="has no permission flags"):
            replace(_spec("codex"), permission_flags=_APPROVAL_FLAGS)

    @pytest.mark.parametrize("backend", ["gemini", "codex"])
    def test_only_a_cli_agent_has_a_permission_mode_mapping(self, backend: str) -> None:
        mapping = (("safe", ("--approval-mode", "plan")), ("allow_all", ("--approval-mode", "yolo")))

        with pytest.raises(ValueError, match="only a CLI agent has permission_mode_args"):
            replace(_spec(backend), permission_mode_args=mapping)

    @pytest.mark.parametrize("ask_mode", [None, "", "  "])
    def test_an_acp_agent_names_the_session_mode_in_which_it_asks(self, ask_mode: str | None) -> None:
        with pytest.raises(ValueError, match="needs the session mode it reports once it asks the host"):
            replace(_spec("gemini"), acp_ask_mode=ask_mode)

    @pytest.mark.parametrize("backend", ["claude", "codex"])
    def test_only_an_acp_agent_has_an_ask_mode(self, backend: str) -> None:
        with pytest.raises(ValueError, match="only an ACP agent has an acp_ask_mode"):
            replace(_spec(backend), acp_ask_mode="default")

    @pytest.mark.parametrize("backend", ["gemini", "qwen", "codex"])
    def test_an_agent_that_is_not_a_cli_process_needs_the_args_that_start_it(self, backend: str) -> None:
        with pytest.raises(ValueError, match="needs the launch_args that start it"):
            replace(_spec(backend), launch_args=())


class TestPermissionFlags:
    def test_a_flag_that_takes_a_value_is_a_flag_of_the_set(self) -> None:
        with pytest.raises(ValueError, match=r"must be flags of the set: \['--mode'\]"):
            PermissionFlags(names=frozenset({"--yolo"}), value_names=frozenset({"--mode"}))

    def test_the_flags_are_taken_out_with_the_value_of_the_one_that_takes_it(self) -> None:
        kept, removed = _APPROVAL_FLAGS.strip(["--approval-mode", "yolo", "-m", "pro", "--yolo", "--sandbox=true"])

        assert kept == ["-m", "pro", "--sandbox=true"]
        assert removed == ["--approval-mode", "yolo", "--yolo"]

    def test_the_inline_spelling_of_a_flag_that_takes_a_value_is_taken_out_too(self) -> None:
        assert _APPROVAL_FLAGS.strip(["--approval-mode=yolo", "-m", "pro"]) == (["-m", "pro"], ["--approval-mode=yolo"])

    def test_a_flag_that_takes_no_value_leaves_the_arg_after_it_alone(self) -> None:
        assert _APPROVAL_FLAGS.strip(["-y", "keep"]) == (["keep"], ["-y"])

    def test_nothing_is_taken_out_of_args_without_such_a_flag(self) -> None:
        assert _APPROVAL_FLAGS.strip([]) == ([], [])
        assert _APPROVAL_FLAGS.strip(["--sandbox", "-m", "pro"]) == (["--sandbox", "-m", "pro"], [])

    def test_an_agent_without_flags_is_given_its_args_back(self) -> None:
        args = ["-c", 'model="o3"', "--yolo"]

        assert _spec("codex").strip_permission_flags(args) == (args, [])

    def test_a_flag_is_the_host_s_for_one_agent_and_the_user_s_for_another(self) -> None:
        args = ["-c", 'model="o3"', "--yolo"]

        assert _spec("gemini").strip_permission_flags(args) == (["-c", 'model="o3"'], ["--yolo"])
        assert _spec("claude").strip_permission_flags(args) == (args, [])
        assert _spec("claude").strip_permission_flags(["--full-auto", "-p"]) == (["-p"], ["--full-auto"])


class TestWhatASpecDerives:
    def test_the_permission_modes_of_a_cli_agent_and_the_args_of_each(self) -> None:
        claude = _spec("claude")

        assert claude.permission_modes == ("safe", "allow_all")
        assert claude.permission_args("safe") == ("--permission-mode", "default")
        assert claude.permission_args("allow_all") == ("--permission-mode", "bypassPermissions")
        assert claude.permission_args("plan") is None

    @pytest.mark.parametrize("backend", ["codex", "gemini", "qwen"])
    def test_an_agent_that_is_not_a_cli_process_has_no_mode_to_map(self, backend: str) -> None:
        assert _spec(backend).permission_modes == ()
        assert _spec(backend).permission_args("safe") is None

    def test_the_home_of_an_agent_is_where_its_variable_points_or_under_the_user_home(self) -> None:
        codex = _spec("codex")

        assert codex.resolve_home({"HOME": "/home/u"}) == Path("/home/u/.codex")
        assert codex.resolve_home({"HOME": "/home/u", "CODEX_HOME": "/persistent/.codex"}) == Path("/persistent/.codex")

    def test_a_variable_that_stands_in_for_the_user_home_leaves_the_agent_its_own_subdirectory(self) -> None:
        gemini = _spec("gemini")

        assert gemini.resolve_home({"HOME": "/home/u", "GEMINI_CLI_HOME": "/accounts/work"}) == Path(
            "/accounts/work/.gemini"
        )
        assert gemini.isolated_env(Path("/accounts/work")) == {
            "GEMINI_CLI_HOME": "/accounts/work",
            "GEMINI_FORCE_FILE_STORAGE": "true",
        }
        assert gemini.relocation_vars == ("GEMINI_CLI_HOME", "GEMINI_FORCE_FILE_STORAGE")

    def test_a_login_for_a_browser_elsewhere_uses_the_remote_args_when_the_agent_has_them(self) -> None:
        codex = _spec("codex")

        assert codex.login_command(remote=False) == ("login",)
        assert codex.login_command(remote=True) == ("login", "--device-auth")
        assert _spec("claude").login_command(remote=True) == _spec("claude").login_command(remote=False)
