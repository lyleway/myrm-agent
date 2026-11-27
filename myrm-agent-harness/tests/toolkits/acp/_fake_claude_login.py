"""A stand-in for the ``claude`` executable that logs in the way the real one does on macOS: outside its home.

The real CLI prints a URL, asks for the code the page shows, and keeps the login in the system Keychain, so the
credential file a file-only check looks for never exists. The stand-in keeps its login in a ``keychain`` file next to
the configuration directory it was relocated into (``CLAUDE_CONFIG_DIR``, a variable every launch of the CLI keeps), and
answers the three commands the harness drives: ``auth login``, ``auth status`` and ``auth logout``.

The code it accepts is ``good-code``; ``ghost-code`` makes it report a success without keeping a login; any other is
refused, and it asks for the code again as it exits, as the real one does. A ``logout_fails`` file next to the configuration directory makes its logout fail and leave the login standing;
what its logout and its status saw of the operator's provider key is written to ``logout_env`` and
``status_env``.

[OUTPUT]
- FakeClaude: The installed stand-in with the paths its tests look at.
- install_fake_claude: Install the stand-in under a directory.

[POS]
Test support of the login session and logout tests.
"""

from __future__ import annotations

import os
import stat
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

_IMPLEMENTATION = textwrap.dedent(
    """\
    import json
    import os
    import pathlib
    import sys

    config = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"])
    keychain = config.parent / "keychain"
    args = sys.argv[1:]

    if args[:2] == ["auth", "login"]:
        print("Opening browser to sign in\\u2026")
        print("If the browser didn't open, visit:")
        print("https://claude.ai/oauth/authorize?z-FAKE")
        sys.stdout.write("Paste code here if prompted > ")
        sys.stdout.flush()
        code = sys.stdin.readline().strip()
        if code == "good-code":
            keychain.write_text("logged-in")
            print("\\nLogin successful.")
            sys.exit(0)
        if code == "ghost-code":
            print("\\nLogin successful.")
            sys.exit(0)
        print("\\nCouldn't log in: That code wasn't accepted.")
        sys.stdout.write("Paste code here if prompted > ")
        sys.stdout.flush()
        sys.exit(1)

    if args[:2] == ["auth", "status"]:
        (config.parent / "status_env").write_text(os.environ.get("ANTHROPIC_API_KEY", ""))
        logged_in = keychain.exists()
        print(json.dumps({"loggedIn": logged_in, "authMethod": "claude.ai" if logged_in else "none"}))
        sys.exit(0 if logged_in else 1)

    if args[:2] == ["auth", "logout"]:
        (config.parent / "logout_env").write_text(os.environ.get("ANTHROPIC_API_KEY", ""))
        if (config.parent / "logout_fails").exists():
            print("Could not reach the keychain.", file=sys.stderr)
            sys.exit(1)
        keychain.unlink(missing_ok=True)
        print("Successfully logged out from your Anthropic account.")
        sys.exit(0)

    sys.exit(2)
    """
)


@dataclass(frozen=True, slots=True)
class FakeClaude:
    """The installed stand-in and the places its tests look at."""

    executable: str
    config_dir: Path

    @property
    def keychain(self) -> Path:
        """Where the stand-in keeps its login: outside the configuration directory."""
        return self.config_dir.parent / "keychain"

    @property
    def logout_env(self) -> Path:
        """What the provider key looked like to its last logout."""
        return self.config_dir.parent / "logout_env"

    @property
    def status_env(self) -> Path:
        """What the provider key looked like to its last status check."""
        return self.config_dir.parent / "status_env"

    @property
    def logout_fails(self) -> Path:
        """Create this file to make its logout fail and leave the login standing."""
        return self.config_dir.parent / "logout_fails"

    def env(self, **extra: str) -> dict[str, str]:
        """The environment a login or logout of this stand-in runs in."""
        return {**os.environ, "CLAUDE_CONFIG_DIR": str(self.config_dir), **extra}


def install_fake_claude(root: Path) -> FakeClaude:
    """Install the stand-in as ``<root>/bin/claude`` with a configuration directory ``<root>/home/config``."""
    implementation = root / "fake_claude.py"
    implementation.write_text(_IMPLEMENTATION, encoding="utf-8")
    executable = root / "bin" / "claude"
    executable.parent.mkdir()
    executable.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{implementation}" "$@"\n', encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    config_dir = root / "home" / "config"
    config_dir.mkdir(parents=True)
    return FakeClaude(executable=str(executable), config_dir=config_dir)
