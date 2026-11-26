"""Holding the command lines the runtime sends to a CLI to what the CLI's own ``--help`` says it accepts.

A CLI releases on its own schedule, so a flag it renames or a value it stops listing breaks the runtime without
any of our code changing. The three CLI families print their options in three shapes (commander for Claude, clap for
Codex, yargs for Gemini and Qwen); ``parse_options`` reads all of them into one table, and ``check_args`` is the
single judge of a command line, so a rename is caught the same way whichever CLI made it.

A value is judged against the list the CLI advertises for its option. A value the list does not carry may still be
accepted (Claude takes ``default`` as an alias of its prompting mode); the probe decides, and only a probe that is
shown to reject garbage counts: a CLI that accepts anything at parse time proves nothing about a value.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest

# Names a probe passes as a value no CLI lists; a probe that accepts it cannot tell a good value from a bad one.
_GARBAGE_VALUE = "__not_a_listed_value__"
_HELP_TIMEOUT_SECONDS = 90

# An option starts a help entry when it opens a line shallower than any wrapped description.
_ENTRY_START = re.compile(r"^ {1,7}-")
_OPTION_NAME = re.compile(r"(?<![\w-])(--[A-Za-z0-9][\w-]*|-[A-Za-z0-9])(?![\w-])")
_SIGNATURE_GAP = re.compile(r"\s{2,}")
# Commander and yargs: ``choices: "a", "b"``; clap: ``possible values: a, b``.
_QUOTED_CHOICES = re.compile(r"choices:\s*((?:\"[^\"]*\"\s*,?\s*)+)")
_PLAIN_CHOICES = re.compile(r"possible values:\s*([^\]]+)\]")
_YARGS_VALUE_TYPE = re.compile(r"\[(?:string|number|array)\]")

# The subcommands whose own options the runtime uses, by CLI. A word after these names opens their help scope.
SUBCOMMANDS: Mapping[str, frozenset[str]] = {"codex": frozenset({"exec", "resume"})}


@dataclass(frozen=True, slots=True)
class CliOption:
    """One option as the CLI describes it."""

    names: tuple[str, ...]
    takes_value: bool
    choices: frozenset[str] | None


def parse_options(help_text: str) -> dict[str, CliOption]:
    """Every option of a ``--help`` text by each of its names; the three CLI families' layouts read the same way."""
    entries: list[list[str]] = []
    for line in help_text.splitlines():
        if _ENTRY_START.match(line):
            entries.append([line])
        elif entries and line.strip():
            entries[-1].append(line)

    options: dict[str, CliOption] = {}
    for entry in entries:
        head = entry[0].strip()
        signature = _SIGNATURE_GAP.split(head, maxsplit=1)[0]
        names = tuple(_OPTION_NAME.findall(signature))
        if not names:
            continue
        text = " ".join(" ".join(part.split()) for part in entry)
        option = CliOption(
            names=names,
            takes_value="<" in signature or "[" in signature or _YARGS_VALUE_TYPE.search(text) is not None,
            choices=_choices_in(text),
        )
        options.update(dict.fromkeys(names, option))
    return options


def _choices_in(text: str) -> frozenset[str] | None:
    quoted = _QUOTED_CHOICES.search(text)
    if quoted is not None:
        return frozenset(re.findall(r'"([^"]*)"', quoted.group(1)))
    plain = _PLAIN_CHOICES.search(text)
    if plain is not None:
        return frozenset(value.strip() for value in plain.group(1).split(",") if value.strip())
    return None


Probe = Callable[[Sequence[str], str, str], bool]
Scopes = Mapping[tuple[str, ...], Mapping[str, CliOption]]


def check_args(
    options_by_scope: Scopes,
    args: Sequence[str],
    *,
    subcommands: frozenset[str] = frozenset(),
    positionals: frozenset[str] = frozenset(),
    probe: Probe | None = None,
) -> list[str]:
    """What the CLI would refuse in ``args`` (the command line without the executable), empty when it takes all.

    ``options_by_scope`` holds the options of the top level (key ``()``) and of each subcommand chain the runtime
    uses. A word in ``subcommands`` moves the following options into that chain's scope; a word in ``positionals``
    is an argument the runtime passes on purpose (a session id), not a value.
    """
    problems: list[str] = []
    scope: tuple[str, ...] = ()
    index = 0
    while index < len(args):
        token = args[index]
        index += 1
        if not token.startswith("-"):
            if token in subcommands and (*scope, token) in options_by_scope:
                scope = (*scope, token)
            elif token not in positionals:
                problems.append(f"{token!r} is neither an option, a known subcommand nor an expected argument")
            continue
        name, has_inline, inline = token.partition("=")
        option = options_by_scope[scope].get(name)
        where = " ".join(scope) or "the top level"
        if option is None:
            problems.append(f"{name} is not an option of {where}")
            # Its value, if it has one, is not a second finding.
            if not has_inline and index < len(args) and _is_word(args[index], subcommands, positionals):
                index += 1
            continue
        if has_inline:
            value: str | None = inline
        elif option.takes_value and index < len(args) and not args[index].startswith("-"):
            value = args[index]
            index += 1
        else:
            value = None
        unlisted = value is not None and option.choices is not None and value not in option.choices
        if unlisted and (probe is None or not probe(scope, name, str(value))):
            problems.append(f"{name} {value} is not among {sorted(option.choices or ())} of {where}")
    return problems


def _is_word(token: str, subcommands: frozenset[str], positionals: frozenset[str]) -> bool:
    return not token.startswith("-") and token not in subcommands and token not in positionals


def required_clis() -> frozenset[str]:
    """The CLIs a run must have: a missing one fails instead of skipping (``MYRM_CONTRACT_REQUIRE_CLIS``, CI)."""
    return frozenset(filter(None, os.environ.get("MYRM_CONTRACT_REQUIRE_CLIS", "").split(",")))


def cli_binary(name: str) -> str:
    """The executable of CLI ``name``; skips the test, or fails it when the run requires the CLI."""
    found = shutil.which(name)
    if found is not None:
        return found
    if name in required_clis():
        pytest.fail(f"the {name} CLI is required by MYRM_CONTRACT_REQUIRE_CLIS but is not installed")
    pytest.skip(f"{name} CLI not installed")


def probe_env(home: Path) -> dict[str, str]:
    """The environment help and parse probes run in: no login, no model, and no write into the host's own CLI homes."""
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        config_dir = home / var.lower()
        # Codex refuses to start when its home does not exist.
        config_dir.mkdir(exist_ok=True)
        env[var] = str(config_dir)
    return env


def run_probe(
    argv: Sequence[str], env: Mapping[str, str], cwd: Path, *, stdin_text: str | None = None
) -> tuple[int, str]:
    """Run ``argv`` to completion and return its exit code with stdout and stderr; ``stdin_text`` is its whole input."""
    done = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=_HELP_TIMEOUT_SECONDS,
        check=False,
        env=dict(env),
        cwd=cwd,
        input=stdin_text,
        stdin=subprocess.DEVNULL if stdin_text is None else None,
    )
    return done.returncode, done.stdout + done.stderr


@cache
def _help_cached(binary: str, scope: tuple[str, ...], home: str) -> str:
    code, text = run_probe([binary, *scope, "--help"], probe_env(Path(home)), Path(home))
    if code != 0:
        pytest.fail(f"`{binary} {' '.join(scope)} --help` exited {code}:\n{text[:400]}")
    return text


def options_by_scope(
    binary: str, scopes: Sequence[tuple[str, ...]], home: Path
) -> dict[tuple[str, ...], dict[str, CliOption]]:
    """The parsed options of each help scope of ``binary``."""
    return {scope: parse_options(_help_cached(binary, scope, str(home))) for scope in scopes}


def parse_probe(binary: str, home: Path) -> Probe:
    """A probe asking the binary to parse ``<option> <value>`` and show its help, which exits 0 only for a value it takes.

    Calibrated per option: a value the CLI rejects as garbage must make it fail, or the probe refuses to vouch for
    the value (an argument parser that accepts anything, or one that prints help before it validates).
    """
    env = probe_env(home)

    def exits_zero(scope: Sequence[str], option: str, value: str) -> bool:
        code, _ = run_probe([binary, *scope, option, value, "--help"], env, home)
        return code == 0

    def probe(scope: Sequence[str], option: str, value: str) -> bool:
        return exits_zero(scope, option, value) and not exits_zero(scope, option, _GARBAGE_VALUE)

    return probe
