"""What the CLI of an agent says about a login, asked in the environment a delegated turn would run it in.

[POS]
Tests for app/services/external_agents/login_state.py.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from myrm_agent_harness.toolkits.acp.toolchains import manager

from app.services.external_agents.login_state import login_state

pytestmark = pytest.mark.skipif(os.name != "posix", reason="a shell script stands in for the CLI")

_REPORTS_A_LOGIN = (
    '#!/bin/sh\n/usr/bin/env > "$(dirname "$0")/seen-env.txt"\necho \'{"loggedIn": true, "authMethod": "claude.ai"}\'\n'
)


def _install(directory: Path) -> Path:
    """A ``claude`` that reports a login and records the environment it was asked in."""
    directory.mkdir(parents=True)
    cli = directory / "claude"
    cli.write_text(_REPORTS_A_LOGIN, encoding="utf-8")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    return cli


def _seen_by(cli: Path) -> dict[str, str]:
    lines = (cli.parent / "seen-env.txt").read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


@pytest.fixture(autouse=True)
def _hermetic_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


class TestTheCliIsAskedAsATurnWouldRunIt:
    async def test_it_reports_the_login_the_cli_says_is_there(self, tmp_path: Path) -> None:
        cli = _install(tmp_path / "bin")

        state = await login_state("claude", str(cli))

        assert state.authenticated

    async def test_the_operators_other_secrets_stay_behind(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cli = _install(tmp_path / "bin")
        monkeypatch.setenv("OPERATOR_SECRET", "hunter2")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-operator")

        await login_state("claude", str(cli))

        seen = _seen_by(cli)
        assert "OPERATOR_SECRET" not in seen
        assert "ANTHROPIC_API_KEY" not in seen
        assert "PATH" in seen

    async def test_the_variable_that_moves_its_own_login_home_is_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cli = _install(tmp_path / "bin")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))

        await login_state("claude", str(cli))

        assert _seen_by(cli)["CLAUDE_CONFIG_DIR"] == str(tmp_path / "config")

    async def test_a_cli_the_toolchain_installed_finds_the_node_of_that_toolchain_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        node_bin = tmp_path / "toolchains" / f"node-{manager.NODE_VERSION}" / "bin"
        node_bin.mkdir(parents=True)
        monkeypatch.setenv(manager.TOOLCHAIN_DIR_ENV, str(tmp_path / "toolchains"))
        cli = _install(tmp_path / "toolchains" / "claude" / "bin")

        await login_state("claude", str(cli))

        assert _seen_by(cli)["PATH"].split(os.pathsep)[0] == str(node_bin)
