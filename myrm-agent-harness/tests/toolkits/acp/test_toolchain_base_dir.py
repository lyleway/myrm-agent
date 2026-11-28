"""Where the isolated toolchain lives: one directory a host can move, which the installer, the detector and the launch
environment all follow, read when asked and not when the package was imported."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.core.backend_detector import BackendDetector
from myrm_agent_harness.toolkits.acp.toolchains import (
    TOOLCHAIN_DIR_ENV,
    IsolatedToolchainManager,
    toolchain_base_dir,
    toolchain_node_bin,
)
from myrm_agent_harness.toolkits.acp.toolchains.manager import NODE_VERSION


@pytest.fixture(autouse=True)
def _a_machine_without_a_named_toolchain_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOOLCHAIN_DIR_ENV, raising=False)


def _home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def test_without_a_named_directory_the_toolchain_is_under_the_users_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home(monkeypatch, tmp_path)

    assert toolchain_base_dir() == tmp_path / ".myrm-agent" / "toolchains"


def test_a_named_directory_is_followed_the_moment_it_is_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unnamed = toolchain_base_dir()

    monkeypatch.setenv(TOOLCHAIN_DIR_ENV, str(tmp_path / "tools"))
    named = toolchain_base_dir()
    monkeypatch.setenv(TOOLCHAIN_DIR_ENV, str(tmp_path / "other"))

    assert (named, toolchain_base_dir()) == (tmp_path / "tools", tmp_path / "other")
    assert unnamed != named


def test_a_name_starting_at_the_home_directory_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home(monkeypatch, tmp_path)
    monkeypatch.setenv(TOOLCHAIN_DIR_ENV, "~/cli-tools")

    assert toolchain_base_dir() == tmp_path / "cli-tools"


def test_a_relative_name_becomes_an_absolute_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(TOOLCHAIN_DIR_ENV, "cli-tools")

    assert toolchain_base_dir().is_absolute()
    assert toolchain_base_dir() == tmp_path / "cli-tools"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_name_names_nothing(blank: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home(monkeypatch, tmp_path)
    monkeypatch.setenv(TOOLCHAIN_DIR_ENV, blank)

    assert toolchain_base_dir() == tmp_path / ".myrm-agent" / "toolchains"


def test_the_installer_works_in_the_named_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOOLCHAIN_DIR_ENV, str(tmp_path / "tools"))

    manager = IsolatedToolchainManager()

    assert manager.base_dir == tmp_path / "tools"
    assert manager.bin_dir == tmp_path / "tools" / "bin"
    assert manager.bin_dir.is_dir()


def test_a_cli_in_the_named_directory_is_launched_with_the_node_of_that_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node_bin = tmp_path / "tools" / f"node-{NODE_VERSION}" / "bin"
    node_bin.mkdir(parents=True)
    monkeypatch.setenv(TOOLCHAIN_DIR_ENV, str(tmp_path / "tools"))

    assert toolchain_node_bin(str(tmp_path / "tools" / "bin" / "claude")) == node_bin
    assert toolchain_node_bin("/usr/local/bin/claude") is None


@pytest.mark.skipif(sys.platform == "win32", reason="the executable bit is a POSIX notion")
def test_the_detector_finds_a_cli_the_named_directory_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli = tmp_path / "tools" / "bin" / "claude"
    cli.parent.mkdir(parents=True)
    cli.touch()
    cli.chmod(0o755)
    monkeypatch.setenv(TOOLCHAIN_DIR_ENV, str(tmp_path / "tools"))
    monkeypatch.setenv("PATH", "")

    assert BackendDetector()._find_executable("claude") == str(cli)
