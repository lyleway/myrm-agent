#!/usr/bin/env python3
"""Fresh-install smoke: the built wheel, alone in an empty environment, delegates one turn to a stub ACP agent.

The unit suite runs against the source tree with every extra installed, so it cannot see a dependency declared under
the wrong extra, a module the wheel leaves out, or an import that works only because the repository is on the path.
This installs the wheel the way a user of the framework does and delegates through its public surface
(``myrm_agent_harness.api.delegation``) twice. Without extras: the surface imports, a turn to a CLI agent streams its
answer, and a turn to an ACP agent says which extra to install. With the ``acp`` extra: the stub agent's answer
streams and the turn ends, the agent is told which host is talking to it, and the host's own permission broker decides
whether the agent's edit happens.

Usage:
    uv build --wheel --out-dir dist-smoke && python scripts/fresh_install_smoke.py dist-smoke/*.whl

[INPUT]
- argv: the wheel; ``uv`` on PATH; ``tests/toolkits/acp/_mock_acp_agent.py`` (the stub agent)

[OUTPUT]
- main(): exit 0 when both phases behave; otherwise the failing phase and what its driver printed

[POS]
``fresh-install`` job of ``.github/workflows/harness-test.yml``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parent.parent
_STUB_AGENT = _HARNESS_ROOT / "tests" / "toolkits" / "acp" / "_mock_acp_agent.py"
_DRIVER = Path(__file__).with_name("fresh_install_smoke_driver.py")
_STEP_TIMEOUT_SECONDS = 600


def _run(command: Sequence[str | Path], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_STEP_TIMEOUT_SECONDS,
        check=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wheel", type=Path, help="the built myrm_agent_harness wheel")
    wheel = parser.parse_args(argv).wheel.resolve()
    if not wheel.is_file():
        print(f"fresh-install smoke: {wheel} is not a file", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="myrm-fresh-install-") as tmp:
        workdir = Path(tmp)
        venv = workdir / "venv"
        python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        created = _run(["uv", "venv", venv, "--python", f"{sys.version_info.major}.{sys.version_info.minor}"], workdir)
        if created.returncode != 0:
            print(created.stderr, file=sys.stderr)
            return 2

        # ``-I`` keeps the repository (and anything else on PYTHONPATH) out of the driver's imports.
        for phase, requirement in (("extra-missing", str(wheel)), ("stub-turn", f"{wheel}[acp]")):
            installed = _run(["uv", "pip", "install", "--python", python, requirement], workdir)
            if installed.returncode != 0:
                print(f"fresh-install smoke: installing {requirement} failed\n{installed.stderr}", file=sys.stderr)
                return 1
            driven = _run([python, "-I", _DRIVER, phase, _STUB_AGENT], workdir)
            if driven.returncode != 0:
                print(f"fresh-install smoke: {phase} failed\n{driven.stdout}{driven.stderr}", file=sys.stderr)
                return 1
            print(f"fresh-install smoke: {phase} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
