"""Symbolic links for tests that build the trees a hostile package can ship.

[INPUT]
- link (POS: where the symbolic link is made)
- target (POS: what it points to; it need not exist)

[OUTPUT]
- make_symlink(link, target): create the link, or skip the test where the platform offers none

[POS]
The one place that decides a symbolic link test is skipped, not failed, on a platform that cannot create one (Windows
without the privilege), so the tests that pin how a walk, an audit or an installer treats a link state their tree and
not the skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def make_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("symbolic links are not available")
