"""Architecture test: the dev stack's lifecycle scripts never abort on a state file that is already gone."""

from __future__ import annotations

from pathlib import Path

_DEV_STACK = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "lib" / "dev_stack"


def test_state_file_removals_tolerate_a_file_another_process_removed_first() -> None:
    """A reset or a heal runs under ``set -e`` and kills the frontend, which removes its own lock while the script removes it too.

    A ``rm`` that moves files to the trash fails for a path that is already gone, and the unguarded call aborted the reset
    after the frontend was killed, leaving the stack half reset.
    """
    scripts = sorted(_DEV_STACK.glob("*.sh"))
    assert scripts, f"no dev stack scripts under {_DEV_STACK}"
    unguarded = [
        f"{script.name}:{number}: {line.strip()}"
        for script in scripts
        for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), start=1)
        if line.lstrip().startswith("rm -f ") and "|| true" not in line
    ]
    assert not unguarded, "state file removals that can abort a reset or a heal:\n" + "\n".join(unguarded)
