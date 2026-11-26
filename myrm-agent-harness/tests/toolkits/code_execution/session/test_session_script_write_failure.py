"""A command whose script cannot be written is not run: the script is what keeps a command from breaking the shell.

A POSIX session runs every command as ``bash <script>``. Without the script an open heredoc or quote would swallow the
output markers and an ``exit`` would end the shell, so the session refuses the command with the reason and keeps the
shell for the commands after it.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.code_execution.session import LocalPersistentSession
from tests.toolkits.code_execution._session_helpers import make_config

_REFUSAL = re.compile(r"The command was not run: its script could not be written \(.+\)\.")


@pytest.mark.skipif(sys.platform == "win32", reason="a Windows shell takes the command as it is, with no script")
async def test_a_command_whose_script_cannot_be_written_is_not_run(tmp_path: Path) -> None:
    # A file where the scratch directory of the workspace belongs makes every script unwritable.
    scratch = tmp_path / ".myrm"
    scratch.write_text("occupied", encoding="utf-8")
    ran = tmp_path / "ran"
    session = LocalPersistentSession(make_config(work_dir=str(tmp_path)))
    await session.start()
    try:
        refused = await session.execute(f"touch {shlex.quote(str(ran))}")

        assert not refused.success
        assert refused.error is not None
        assert _REFUSAL.fullmatch(refused.error), refused.error
        assert refused.stderr == refused.error
        assert not ran.exists(), "the command ran without its script"

        scratch.unlink()
        after = await session.execute("echo after")
        assert after.success, "the refusal broke the shell"
        assert after.stdout.strip() == "after"
    finally:
        await session.close()
