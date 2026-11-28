"""The login a user is handed to run in a terminal: which command, in whose home, spelled for which shell.

The POSIX spelling is run by a real ``sh`` against a stand-in CLI, so what a person pastes is what is proven. The two
Windows spellings are held to the exact text their shells read; no Windows shell runs here.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import (
    TerminalLogin,
    default_shell,
    known_backends,
    profile_for,
    terminal_login,
)
from myrm_agent_harness.toolkits.acp.toolchains import manager

_POSIX = os.name == "posix"
_PRINTS_WHAT_IT_GOT = (
    "import json, os, sys\n"
    "path = os.environ['PATH'].split(os.pathsep)\n"
    "print(json.dumps({'argv': sys.argv[1:], 'config': os.environ.get('CLAUDE_CONFIG_DIR'), 'path_head': path[0], 'path_rest': os.pathsep.join(path[1:])}))\n"
)
_CLEAN_PATH = "/usr/bin:/bin"


def _install_stand_in(directory: Path) -> Path:
    """A ``claude`` that prints the arguments and environment it was started with."""
    directory.mkdir(parents=True)
    implementation = directory / "implementation.py"
    implementation.write_text(_PRINTS_WHAT_IT_GOT, encoding="utf-8")
    executable = directory / "claude"
    executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{implementation}" "$@"\n', encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def _typed_to_sh(command: str) -> dict[str, object]:
    """What the stand-in reported after a POSIX shell, with only a clean ``PATH``, ran ``command``."""
    done = subprocess.run(["sh", "-c", command], capture_output=True, text=True, env={"PATH": _CLEAN_PATH}, check=True)
    return json.loads(done.stdout)


class TestWhatIsTyped:
    @pytest.mark.parametrize("backend", sorted(known_backends()))
    def test_it_is_the_login_command_of_the_profile(self, backend: str) -> None:
        profile = profile_for(backend)
        assert profile is not None

        login = terminal_login(profile, "/opt/agents/bin/agent")

        assert login.argv == ("/opt/agents/bin/agent", *profile.login_args)
        assert login.env == ()
        assert login.path_prefix is None

    @pytest.mark.parametrize("backend", sorted(known_backends()))
    def test_an_account_keeps_the_login_in_its_own_home(self, backend: str, tmp_path: Path) -> None:
        profile = profile_for(backend)
        assert profile is not None
        home = tmp_path / "work"

        login = terminal_login(profile, "/opt/agents/bin/agent", account_home=home)

        assert dict(login.env) == profile.isolated_env(home)
        assert login.env  # every agent has some variable that moves its login

    def test_the_cli_is_typed_by_name_when_the_terminal_finds_this_very_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = profile_for("claude")
        assert profile is not None
        cli = _install_stand_in(tmp_path / "bin")
        monkeypatch.setenv("PATH", f"{cli.parent}{os.pathsep}{os.environ['PATH']}")

        assert terminal_login(profile, str(cli)).argv[0] == "claude"

    def test_the_cli_is_typed_by_path_when_the_terminal_would_find_another(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = profile_for("claude")
        assert profile is not None
        on_path = _install_stand_in(tmp_path / "on-path")
        installed = _install_stand_in(tmp_path / "installed")
        monkeypatch.setenv("PATH", f"{on_path.parent}{os.pathsep}{os.environ['PATH']}")

        assert terminal_login(profile, str(installed)).argv[0] == str(installed)

    def test_the_cli_is_typed_by_path_when_the_terminal_finds_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = profile_for("claude")
        assert profile is not None
        installed = _install_stand_in(tmp_path / "installed")
        monkeypatch.setenv("PATH", _CLEAN_PATH)

        assert terminal_login(profile, str(installed)).argv[0] == str(installed)

    @pytest.mark.skipif(not _POSIX, reason="a symlink on PATH")
    def test_a_link_to_the_cli_on_the_path_is_the_same_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = profile_for("claude")
        assert profile is not None
        installed = _install_stand_in(tmp_path / "installed")
        (tmp_path / "links").mkdir()
        (tmp_path / "links" / "claude").symlink_to(installed)
        monkeypatch.setenv("PATH", f"{tmp_path / 'links'}{os.pathsep}{os.environ['PATH']}")

        assert terminal_login(profile, str(installed)).argv[0] == "claude"

    def test_a_cli_the_toolchain_installed_brings_its_node_to_the_front_of_the_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = profile_for("claude")
        assert profile is not None
        node_bin = tmp_path / "toolchains" / f"node-{manager.NODE_VERSION}" / "bin"
        node_bin.mkdir(parents=True)
        monkeypatch.setenv(manager.TOOLCHAIN_DIR_ENV, str(tmp_path / "toolchains"))
        installed = tmp_path / "toolchains" / "claude" / "bin" / "claude"

        login = terminal_login(profile, str(installed))

        assert login.path_prefix == str(node_bin)
        assert terminal_login(profile, "/usr/local/bin/claude").path_prefix is None


@pytest.mark.skipif(not _POSIX, reason="a POSIX shell")
class TestAPosixShell:
    @pytest.mark.parametrize(
        "home_name", ["plain", "with space", "it's", "dollar$HOME", "semi;colon", "back`tick`", 'double"quote', "star*"]
    )
    def test_runs_the_cli_in_the_home_of_the_account_as_it_is_written(self, home_name: str, tmp_path: Path) -> None:
        profile = profile_for("claude")
        assert profile is not None
        cli = _install_stand_in(tmp_path / "bin with space")
        home = tmp_path / home_name

        reached = _typed_to_sh(terminal_login(profile, str(cli), account_home=home).render("posix"))

        assert reached["argv"] == list(profile.login_args)
        assert reached["config"] == str(home)

    def test_gives_a_cli_that_has_no_account_its_own_home(self, tmp_path: Path) -> None:
        profile = profile_for("claude")
        assert profile is not None
        cli = _install_stand_in(tmp_path / "bin")

        reached = _typed_to_sh(terminal_login(profile, str(cli)).render("posix"))

        assert reached["config"] is None

    def test_puts_the_managed_node_ahead_of_the_rest_of_the_path_for_this_command_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = profile_for("claude")
        assert profile is not None
        node_bin = tmp_path / "toolchains" / f"node-{manager.NODE_VERSION}" / "bin"
        node_bin.mkdir(parents=True)
        monkeypatch.setenv(manager.TOOLCHAIN_DIR_ENV, str(tmp_path / "toolchains"))
        cli = _install_stand_in(tmp_path / "toolchains" / "claude" / "bin")
        command = terminal_login(profile, str(cli), account_home=tmp_path / "work").render("posix")

        reached = _typed_to_sh(command)
        after = subprocess.run(
            ["sh", "-c", f'{command} > /dev/null; echo "[$CLAUDE_CONFIG_DIR][$PATH]"'],
            capture_output=True,
            text=True,
            env={"PATH": _CLEAN_PATH},
            check=True,
        )

        assert reached["path_head"] == str(node_bin)
        assert reached["path_rest"] == _CLEAN_PATH
        assert after.stdout.strip() == f"[][{_CLEAN_PATH}]"


class TestEveryShell:
    _LOGIN = TerminalLogin(
        argv=("/opt/cli dir/claude", "auth", "login"),
        env=(("CLAUDE_CONFIG_DIR", "/home/it's"),),
        path_prefix="/opt/node bin",
    )

    def test_a_posix_shell_reads_single_quotes(self) -> None:
        assert self._LOGIN.render("posix") == (
            "CLAUDE_CONFIG_DIR='/home/it'\"'\"'s' PATH='/opt/node bin':\"$PATH\" '/opt/cli dir/claude' auth login"
        )

    def test_powershell_calls_a_path_with_the_call_operator_and_doubles_a_single_quote(self) -> None:
        assert self._LOGIN.render("powershell") == (
            "$env:CLAUDE_CONFIG_DIR = '/home/it''s'; "
            f"$env:PATH = '/opt/node bin{os.pathsep}' + $env:PATH; "
            "& '/opt/cli dir/claude' auth login"
        )

    def test_powershell_doubles_the_typographic_quotes_it_reads_as_single_quotes(self) -> None:
        login = TerminalLogin(argv=("claude",), env=(("CLAUDE_CONFIG_DIR", "C:\\Users\\it\u2019s\u201a"),))

        assert login.render("powershell") == "$env:CLAUDE_CONFIG_DIR = 'C:\\Users\\it\u2019\u2019s\u201a\u201a'; claude"

    def test_cmd_sets_the_variables_for_the_session_and_quotes_what_has_a_space(self) -> None:
        assert self._LOGIN.render("cmd") == (
            'set "CLAUDE_CONFIG_DIR=/home/it\'s" && set "PATH=/opt/node bin;%PATH%" && "/opt/cli dir/claude" auth login'
        )

    @pytest.mark.parametrize("shell", ["posix", "powershell", "cmd"])
    def test_a_plain_command_is_typed_as_it_is(self, shell: str) -> None:
        login = TerminalLogin(argv=("claude", "auth", "login", "--claudeai"))

        assert login.render(shell) == "claude auth login --claudeai"  # type: ignore[arg-type]

    @pytest.mark.parametrize("word", ["a,b", "a=b", "a b", "a;b", "a&b"])
    def test_a_word_a_windows_shell_would_read_as_more_than_one_is_quoted(self, word: str) -> None:
        login = TerminalLogin(argv=("claude", word))

        assert login.render("powershell") == f"claude '{word}'"
        assert login.render("cmd") == f'claude "{word}"'

    @pytest.mark.parametrize("value", ['say "hi"', "100%", "two\nlines"])
    def test_cmd_cannot_spell_what_it_would_read_differently(self, value: str) -> None:
        login = TerminalLogin(argv=("claude",), env=(("CLAUDE_CONFIG_DIR", value),))

        with pytest.raises(ValueError, match=r"cmd\.exe cannot spell"):
            login.render("cmd")
        assert set(login.commands()) == {"posix", "powershell"}

    def test_the_commands_are_those_of_every_shell_that_can_spell_it(self) -> None:
        login = TerminalLogin(argv=("claude", "auth", "login"))

        assert login.commands() == {
            "posix": "claude auth login",
            "powershell": "claude auth login",
            "cmd": "claude auth login",
        }

    def test_a_windows_host_opens_powershell_and_any_other_a_posix_shell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(os, "name", "nt")
        assert default_shell() == "powershell"
        monkeypatch.setattr(os, "name", "posix")
        assert default_shell() == "posix"
