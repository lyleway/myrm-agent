"""A shared mux daemon is not restarted on the strength of a CLI that cannot run or a load that cannot be read.

A checkout that never installed the mux package's dependencies cannot run ``cdmcp-mux-autoconnect status``. That
failure is no evidence that the daemon is down or idle: acting on it would stop the healthy daemon every other Agent
is attached to and then fail to start it again. The preflight therefore refuses such a checkout before any mux step,
and the restart policy treats a healthy daemon with an unreadable status as busy.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from mux import health, load
from mux.health import MuxDaemonState, MuxHealthVerdict

_DEV_DIR = Path(__file__).resolve().parents[3] / "scripts" / "dev"
_PREFLIGHT = _DEV_DIR / "chrome-e2e-preflight.sh"
_MUX_STATUS_LIB = _DEV_DIR / "lib" / "chrome_e2e_preflight" / "mux_status.sh"

_IDLE_STATUS = {"ok": True, "contexts": [], "activeLeases": []}


def _verdict(state: MuxDaemonState) -> MuxHealthVerdict:
    return MuxHealthVerdict(
        state=state,
        owner_pid=4242,
        socket_path="/tmp/cdmcp-mux.sock",
        probe_ok=state is MuxDaemonState.HEALTHY,
        agent_rule="",
        detail="test",
    )


@pytest.fixture(autouse=True)
def _fresh_status_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(load, "_status_cache", None)


def _daemon_state(monkeypatch: pytest.MonkeyPatch, state: MuxDaemonState) -> None:
    monkeypatch.setattr(health, "evaluate_mux_health", lambda: _verdict(state))


def test_restart_is_blocked_when_a_healthy_daemons_load_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _daemon_state(monkeypatch, MuxDaemonState.HEALTHY)
    monkeypatch.setattr(health, "read_mux_status", lambda force=False: None)

    assert health.parallel_load_blocks_mux_restart() is True


def test_restart_is_allowed_for_a_healthy_idle_daemon_whose_status_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    _daemon_state(monkeypatch, MuxDaemonState.HEALTHY)
    monkeypatch.setattr(health, "read_mux_status", lambda force=False: _IDLE_STATUS)

    assert health.parallel_load_blocks_mux_restart() is False


def test_restart_is_allowed_for_a_dead_daemon_without_a_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _daemon_state(monkeypatch, MuxDaemonState.DEAD)
    monkeypatch.setattr(health, "read_mux_status", lambda force=False: None)

    assert health.parallel_load_blocks_mux_restart() is False


@pytest.mark.skipif(shutil.which("node") is None, reason="the mux CLI runs on node")
def test_restart_is_blocked_when_this_checkout_cannot_run_the_mux_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real CLI of a checkout without the package's dependencies crashes on import: no stub of the status read."""
    package = tmp_path / "cdmcp-mux-autoconnect"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    mux_bin = package / "bin" / "cdmcp-mux-autoconnect.mjs"
    mux_bin.write_text("import 'chrome-devtools-mcp-mux/dist/daemon/daemon.js';\n", encoding="utf-8")
    monkeypatch.setenv("CDMCP_MUX_BIN", str(mux_bin))
    _daemon_state(monkeypatch, MuxDaemonState.HEALTHY)

    assert health.read_mux_status(force=True) is None
    assert health.parallel_load_blocks_mux_restart() is True


def _run_guard(tmp_path: Path, *, deps: bool, mux_using: int = 1, bin_exists: bool = True) -> subprocess.CompletedProcess[str]:
    """Run `_require_mux_bin_deps` the way the preflight does: sourced, with the entry's `fail` and globals."""
    package = tmp_path / "cdmcp-mux-autoconnect"
    (package / "bin").mkdir(parents=True)
    mux_bin = package / "bin" / "cdmcp-mux-autoconnect.mjs"
    if bin_exists:
        mux_bin.write_text("", encoding="utf-8")
    if deps:
        (package / "node_modules" / "chrome-devtools-mcp-mux").mkdir(parents=True)
        (package / "node_modules" / "chrome-devtools-mcp-mux" / "package.json").write_text("{}", encoding="utf-8")
    script = "\n".join(
        [
            'fail() { echo "CHROME_E2E_FAIL: $*" >&2; exit 1; }',
            f"MUX_USING={mux_using}",
            f"MUX_BIN={shlex.quote(str(mux_bin))}",
            f"source {shlex.quote(str(_MUX_STATUS_LIB))}",
            "_require_mux_bin_deps",
            "echo GUARD_PASSED",
        ]
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, timeout=30)


def test_guard_refuses_a_checkout_whose_mux_package_has_no_dependencies(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, deps=False)

    assert result.returncode == 1
    assert "GUARD_PASSED" not in result.stdout
    assert "CHROME_E2E_FAIL: cdmcp-mux dependencies are missing" in result.stderr
    assert str(tmp_path / "cdmcp-mux-autoconnect") in result.stderr


def test_guard_passes_a_checkout_with_the_mux_dependencies(tmp_path: Path) -> None:
    result = _run_guard(tmp_path, deps=True)

    assert (result.returncode, result.stdout, result.stderr) == (0, "GUARD_PASSED\n", "")


def test_guard_stays_out_of_the_way_when_the_mux_is_not_in_use_or_has_no_bin(tmp_path: Path) -> None:
    unused = _run_guard(tmp_path / "unused", deps=False, mux_using=0)
    no_bin = _run_guard(tmp_path / "no_bin", deps=False, bin_exists=False)

    assert (unused.returncode, unused.stdout, unused.stderr) == (0, "GUARD_PASSED\n", "")
    assert (no_bin.returncode, no_bin.stdout, no_bin.stderr) == (0, "GUARD_PASSED\n", "")


def test_preflight_runs_the_guard_after_the_status_library_and_before_any_mux_step() -> None:
    text = _PREFLIGHT.read_text(encoding="utf-8")

    status_library = text.index("lib/chrome_e2e_preflight/mux_status.sh")
    guard = text.index("_require_mux_bin_deps")
    first_mux_step = text.index("lib/chrome_e2e_preflight/stage_attach_and_mux.sh")

    assert status_library < guard < first_mux_step
