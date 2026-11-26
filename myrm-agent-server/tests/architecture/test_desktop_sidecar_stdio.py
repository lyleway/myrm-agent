"""The windowed Windows sidecar starts with unusable standard streams; the runtime hook repairs them.

Two launches break a backend that prints text, and the tests start a child the way each parent does:

- From the shell (no console, `--noconsole` build) Python starts with `sys.stdout` and friends set to `None`: a
  library that writes to one raises `AttributeError` (tqdm, which huggingface_hub depends on, does) and
  `faulthandler.enable()` raises `RuntimeError`. Descriptors 0-2 are closed to reproduce that, and the probes
  are standard-library calls, so the guard runs in every environment (the desktop contract job installs no
  progress-bar library).
- With stdout redirected, Python encodes it in the ANSI code page and the first emoji in a startup message raises
  `UnicodeEncodeError`. `PYTHONIOENCODING=cp1252` reproduces the Windows default on any host.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.desktop_release import sidecar_build_module

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="closes descriptors 0-2 through sh")

# Reports through a file because the child has no stdout to report on.
_CHILD = """
import faulthandler
import runpy
import sys

hook, report = sys.argv[1:3]
if hook:
    runpy.run_path(hook)
outcomes = []
# A write is what a progress bar does; a descriptor request is what faulthandler does.
for name, use in (("write", lambda: sys.stderr.write("progress")), ("descriptor", faulthandler.enable)):
    try:
        use()
        outcomes.append(f"{name}: ok")
    except Exception as exc:
        outcomes.append(f"{name}: {type(exc).__name__}")
outcomes.append(str(sys.stdout.isatty() if sys.stdout else None))
with open(report, "w", encoding="utf-8") as handle:
    handle.write("\\n".join(outcomes))
"""

_PRINT_CHILD = """
import runpy
import sys

if sys.argv[1]:
    runpy.run_path(sys.argv[1])
print("\\U0001f40d \\ud800")  # an emoji, then a character no encoding accepts
"""


@pytest.fixture(scope="module")
def build_module() -> ModuleType:
    return sidecar_build_module()


def _start_without_standard_streams(tmp_path: Path, hook: Path | None) -> list[str]:
    report = tmp_path / "report.txt"
    subprocess.run(
        ["sh", "-c", 'exec "$@" <&- >&- 2>&-', "sh", sys.executable, "-c", _CHILD, str(hook or ""), str(report)],
        check=True,
        timeout=120,
    )
    return report.read_text(encoding="utf-8").splitlines()


def test_a_library_cannot_use_the_standard_streams_of_a_process_that_has_none(tmp_path: Path) -> None:
    """The premise of the first half: if Python stops leaving the streams `None`, the hook has no use there."""
    outcomes = _start_without_standard_streams(tmp_path, hook=None)

    assert outcomes == ["write: AttributeError", "descriptor: RuntimeError", "None"]


def test_the_hook_gives_a_library_streams_it_can_use(tmp_path: Path, build_module: ModuleType) -> None:
    outcomes = _start_without_standard_streams(tmp_path, build_module.STDIO_RUNTIME_HOOK)

    assert outcomes == ["write: ok", "descriptor: ok", "False"]


def _print_to_redirected_stdout(tmp_path: Path, hook: Path | None) -> tuple[int, str, bytes]:
    out = tmp_path / "stdout.bin"
    with out.open("wb") as handle:
        completed = subprocess.run(
            [sys.executable, "-c", _PRINT_CHILD, str(hook or "")],
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
    return completed.returncode, completed.stderr.decode("ascii", "replace"), out.read_bytes()


def test_a_legacy_codepage_cannot_print_the_startup_banner(tmp_path: Path) -> None:
    """The premise of the encoding half: if Python stops defaulting to the ANSI code page, drop that half."""
    returncode, stderr, _ = _print_to_redirected_stdout(tmp_path, hook=None)

    assert returncode != 0
    assert "UnicodeEncodeError" in stderr


def test_the_hook_prints_emoji_to_a_redirected_stdout_as_utf8(tmp_path: Path, build_module: ModuleType) -> None:
    returncode, stderr, written = _print_to_redirected_stdout(tmp_path, build_module.STDIO_RUNTIME_HOOK)

    assert returncode == 0, stderr
    assert written == "\U0001f40d ?\n".encode()


def test_the_sidecar_build_runs_the_hook_first(build_module: ModuleType) -> None:
    cmd = build_module.pyinstaller_command(Path("python"), Path("work"))

    assert cmd[cmd.index("--runtime-hook") + 1] == str(build_module.STDIO_RUNTIME_HOOK)
    assert build_module.STDIO_RUNTIME_HOOK.is_file()
