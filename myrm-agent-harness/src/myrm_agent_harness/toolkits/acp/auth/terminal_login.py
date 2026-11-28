"""The login of a CLI agent as a person types it in a terminal.

Some logins cannot be driven from the host: the CLI asks for a code the host would have to relay, keeps the login where
only the CLI may touch it, or runs an interface of its own. The user runs the CLI's own login there, and this describes
the command to run: in the home of a named account when one is given, with the CLI found the way the terminal will
find it, and spelled for each shell the user may be in.

[INPUT]
- toolkits.acp.drivers::DriverSpec (POS: The contract of every known agent: its login, launch, transport and permission facts)
- toolkits.acp.toolchains::toolchain_node_bin (POS: The managed Node a CLI installed by the isolated toolchain needs on PATH)

[OUTPUT]
- Shell: The shells a command can be spelled for.
- TerminalLogin: The command that starts a login, and how to spell it for a shell.
- terminal_login: The login of a profile as typed to start an installed CLI.
- default_shell: The shell of this host's own terminal.

[POS]
Terminal-login description of the ACP auth subsystem. It starts nothing: the CLI runs in the user's terminal, so the
login never passes through the host.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from myrm_agent_harness.toolkits.acp.drivers import DriverSpec
from myrm_agent_harness.toolkits.acp.toolchains import toolchain_node_bin

Shell = Literal["posix", "powershell", "cmd"]

_SHELLS: tuple[Shell, ...] = ("posix", "powershell", "cmd")
# What both Windows shells read as one plain word, so it needs no quotes: ``,`` makes an array in PowerShell and
# ``=`` splits a word in a batch file.
_PLAIN_WORD = re.compile(r"[A-Za-z0-9_./\\:+-]+")
# PowerShell reads these four typographic quotes as the single quote, so each must be doubled like it.
_POWERSHELL_QUOTES = re.compile("(['\u2018\u2019\u201a\u201b])")
# Inside quotes cmd.exe still expands ``%name%``, has no way to write a double quote and ends a command at a newline.
_CMD_UNSPELLABLE = re.compile(r'["%\r\n]')


@dataclass(frozen=True, slots=True)
class TerminalLogin:
    """The command that starts a login, apart from the shell it is typed in.

    ``argv``: the CLI as the terminal will find it, then its login arguments. ``env``: the variables that keep the
    login in an account's home. ``path_prefix``: a directory the CLI needs on ``PATH`` ahead of the rest.
    """

    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()
    path_prefix: str | None = None

    def render(self, shell: Shell) -> str:
        """The command spelled for ``shell``.

        Raises:
            ValueError: ``shell`` cannot spell some part of it (cmd.exe has no way to write ``"``, ``%`` or a newline).
        """
        if shell == "posix":
            return self._posix()
        if shell == "powershell":
            return self._powershell()
        return self._cmd()

    def commands(self) -> dict[Shell, str]:
        """The command spelled for every shell that can spell it."""
        spelled: dict[Shell, str] = {}
        for shell in _SHELLS:
            with contextlib.suppress(ValueError):
                spelled[shell] = self.render(shell)
        return spelled

    def _posix(self) -> str:
        # ``NAME=value command`` scopes the variables to this one command.
        assignments = [f"{name}={shlex.quote(value)}" for name, value in self.env]
        if self.path_prefix is not None:
            assignments.append(f'PATH={shlex.quote(self.path_prefix)}:"$PATH"')
        return " ".join([*assignments, *map(shlex.quote, self.argv)])

    def _powershell(self) -> str:
        statements = [f"$env:{name} = {_powershell_quote(value)}" for name, value in self.env]
        if self.path_prefix is not None:
            statements.append(f"$env:PATH = {_powershell_quote(self.path_prefix + os.pathsep)} + $env:PATH")
        executable, *args = self.argv
        call = executable if _PLAIN_WORD.fullmatch(executable) else f"& {_powershell_quote(executable)}"
        statements.append(" ".join([call, *(a if _PLAIN_WORD.fullmatch(a) else _powershell_quote(a) for a in args)]))
        return "; ".join(statements)

    def _cmd(self) -> str:
        statements = [f'set "{name}={_cmd_value(value)}"' for name, value in self.env]
        if self.path_prefix is not None:
            statements.append(f'set "PATH={_cmd_value(self.path_prefix)};%PATH%"')
        statements.append(" ".join(_cmd_word(word) for word in self.argv))
        return " && ".join(statements)


def terminal_login(profile: DriverSpec, executable: str, *, account_home: Path | None = None) -> TerminalLogin:
    """The login of ``profile`` as typed to start the installed CLI at ``executable``.

    ``account_home``: the home of the account to log in to. Without one the CLI logs in to its own home.
    """
    node_bin = toolchain_node_bin(executable)
    return TerminalLogin(
        argv=(_as_typed(profile.backend, executable), *profile.login_args),
        env=tuple(profile.isolated_env(account_home).items()) if account_home is not None else (),
        path_prefix=str(node_bin) if node_bin is not None else None,
    )


def default_shell() -> Shell:
    """The shell of this host's own terminal."""
    return "powershell" if os.name == "nt" else "posix"


def _as_typed(backend: str, executable: str) -> str:
    """``backend`` when a terminal finds this very executable under that name, otherwise the path of it."""
    on_path = shutil.which(backend)
    if on_path is not None:
        with contextlib.suppress(OSError):
            if os.path.samefile(on_path, executable):
                return backend
    return executable


def _powershell_quote(value: str) -> str:
    return "'" + _POWERSHELL_QUOTES.sub(r"\1\1", value) + "'"


def _cmd_value(value: str) -> str:
    if _CMD_UNSPELLABLE.search(value):
        msg = f"cmd.exe cannot spell {value!r}"
        raise ValueError(msg)
    return value


def _cmd_word(value: str) -> str:
    return value if _PLAIN_WORD.fullmatch(value) else f'"{_cmd_value(value)}"'
