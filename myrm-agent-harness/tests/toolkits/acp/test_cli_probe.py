"""run_probe and cli_base_env: a question put to a CLI is bounded, and sees the environment a turn would see."""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from typing import ClassVar

import pytest

from myrm_agent_harness.toolkits.acp.auth.launch_env import cli_base_env
from myrm_agent_harness.toolkits.acp.core.probe import run_probe
from myrm_agent_harness.toolkits.acp.toolchains import manager as manager_module

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="probes are exercised with POSIX shell scripts")


def _script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class TestRunProbe:
    async def test_returns_what_the_command_printed_and_how_it_ended(self, tmp_path: Path) -> None:
        cli = _script(tmp_path / "cli", 'echo "out $1"; echo "err" >&2; exit 4\n')

        result = await run_probe([cli, "x"], env={})

        assert result is not None
        assert (result.returncode, result.stdout, result.stderr) == (4, "out x", "err")

    async def test_runs_with_exactly_the_given_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PROBE_SECRET", "leak")
        cli = _script(tmp_path / "cli", 'echo "[$PROBE_SECRET][$ONLY_THIS]"\n')

        result = await run_probe([cli], env={"ONLY_THIS": "yes"})

        assert result is not None
        assert result.stdout == "[][yes]"

    async def test_a_command_that_cannot_start_gives_none(self, tmp_path: Path) -> None:
        assert await run_probe([str(tmp_path / "missing")], env={}) is None

    async def test_a_hung_command_gives_none_and_leaves_nothing_running(self, tmp_path: Path) -> None:
        pidfile = tmp_path / "child.pid"
        cli = _script(tmp_path / "cli", f"sleep 300 &\necho $! > {pidfile}\nwait\n")

        result = await run_probe([cli], env={"PATH": os.environ["PATH"]}, timeout_seconds=4)

        assert result is None
        assert not _alive(int(pidfile.read_text()))

    async def test_output_is_capped(self, tmp_path: Path) -> None:
        cli = _script(tmp_path / "cli", "head -c 1000000 /dev/zero | tr '\\\\0' 'a'\n")

        result = await run_probe([cli], env={"PATH": os.environ["PATH"]})

        assert result is not None
        assert 0 < len(result.stdout) <= 64 * 1024

    async def test_a_command_that_reads_stdin_does_not_wait_for_input(self, tmp_path: Path) -> None:
        cli = _script(tmp_path / "cli", "cat\necho done\n")

        result = await run_probe([cli], env={"PATH": os.environ["PATH"]}, timeout_seconds=5)

        assert result is not None
        assert result.stdout == "done"


class TestCliBaseEnv:
    SOURCE: ClassVar[dict[str, str]] = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/me",
        "OPENAI_API_KEY": "sk-secret",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "CLAUDE_CONFIG_DIR": "/data/claude",
        "CODEX_HOME": "/data/codex",
    }

    def test_the_operators_secrets_stay_behind(self) -> None:
        env = cli_base_env("claude", self.SOURCE)

        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == "/home/me"
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env

    def test_only_this_clis_own_login_home_is_inherited(self) -> None:
        claude = cli_base_env("claude", self.SOURCE)
        codex = cli_base_env("/opt/bin/codex", self.SOURCE)

        assert claude["CLAUDE_CONFIG_DIR"] == "/data/claude"
        assert "CODEX_HOME" not in claude
        assert codex["CODEX_HOME"] == "/data/codex"
        assert "CLAUDE_CONFIG_DIR" not in codex

    def test_a_cli_installed_by_the_toolchain_gets_its_node_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(manager_module, "TOOLCHAIN_BASE_DIR", tmp_path)
        node_bin = tmp_path / f"node-{manager_module.NODE_VERSION}" / "bin"
        node_bin.mkdir(parents=True)

        env = cli_base_env(str(tmp_path / "bin" / "claude"), self.SOURCE)

        assert env["PATH"] == f"{node_bin}{os.pathsep}/usr/bin:/bin"

    def test_a_system_cli_keeps_the_path_it_was_given(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(manager_module, "TOOLCHAIN_BASE_DIR", tmp_path)
        (tmp_path / f"node-{manager_module.NODE_VERSION}" / "bin").mkdir(parents=True)

        assert cli_base_env("/usr/local/bin/claude", self.SOURCE)["PATH"] == "/usr/bin:/bin"
        assert cli_base_env("claude", self.SOURCE)["PATH"] == "/usr/bin:/bin"
        assert cli_base_env(None, self.SOURCE)["PATH"] == "/usr/bin:/bin"
