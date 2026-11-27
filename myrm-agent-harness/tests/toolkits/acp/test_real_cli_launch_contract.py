"""The command line of every known agent is still one the installed CLI accepts. Offline: no login, no model.

``resolve_cli_args`` (CLI agents), ``resolve_acp_args`` (ACP agents) and ``resolve_app_server_args`` (the app-server
agent) are the only places that turn a configuration into CLI flags, so the contract is judged on their whole output:
each flag must still be one the CLI prints in its ``--help``, and each value of an enumerated option one it still lists
(or one its parser is shown to take). A CLI that renames ``--approval-mode`` or stops listing a value breaks the runtime
without any of our code changing; this goes red the day the new release ships, not the day a user finds out.

A tier flag that stops advertising its values would make the value check vacuous, so that is a failure of its own, and
the last tests send deliberately wrong command lines through the live judge to prove it still refuses them.

Run with ``-m integration``. A CLI that is not installed skips its tests, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names it.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from myrm_agent_harness.toolkits.acp.auth._launch_args import (
    resolve_acp_args,
    resolve_app_server_args,
    resolve_cli_args,
)
from myrm_agent_harness.toolkits.acp.auth._profiles import known_backends, profile_for
from myrm_agent_harness.toolkits.acp.types import PermissionMode
from tests.toolkits.acp.cli_contract import (
    SUBCOMMANDS,
    Scopes,
    check_args,
    cli_binary,
    options_by_scope,
    parse_probe,
)

pytestmark = pytest.mark.integration

# The help scopes whose options the runtime uses; a CLI without subcommands has only its top level.
_SCOPES: dict[str, list[tuple[str, ...]]] = {"codex": [(), ("app-server",)]}
# The option that carries the permission tier of each CLI launched with one.
_TIER_FLAG = {
    "claude": "--permission-mode",
    "gemini": "--approval-mode",
    "qwen": "--approval-mode",
}
# The agent that is given its approval policy and sandbox with every turn has no tier flag; it is launched with the
# subcommand and every kind of configuration its launch args can carry.
_APP_SERVER_AGENT = "codex"
_APP_SERVER_CONFIG = ["-c", 'model="o3"', "--enable", "some_feature", "--disable", "other_feature", "--strict-config"]


def _launch_args(backend: str, mode: str) -> list[str]:
    """What the runtime starts ``backend`` with: an ACP agent by its contract, a CLI agent by the mode's flags."""
    profile = profile_for(backend)
    assert profile is not None
    if profile.transport == "acp":
        return resolve_acp_args(backend, []).args
    return resolve_cli_args(backend, [], mode)


@pytest.fixture(scope="module")
def probe_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A home the CLIs can read and write while they print help: the host's own CLI homes stay untouched."""
    return tmp_path_factory.mktemp("cli-probe-home")


def _scopes(backend: str, probe_home: Path) -> tuple[str, Scopes]:
    binary = cli_binary(backend)
    return binary, options_by_scope(binary, _SCOPES.get(backend, [()]), probe_home)


def test_every_known_backend_is_judged_by_this_contract() -> None:
    assert set(known_backends()) == {*_TIER_FLAG, _APP_SERVER_AGENT}


@pytest.mark.parametrize("backend", sorted(_TIER_FLAG))
@pytest.mark.parametrize("mode", get_args(PermissionMode))
def test_the_launch_args_of_a_permission_mode_are_accepted(backend: str, mode: str, probe_home: Path) -> None:
    binary, scopes = _scopes(backend, probe_home)

    problems = check_args(
        scopes,
        _launch_args(backend, mode),
        subcommands=SUBCOMMANDS.get(backend, frozenset()),
        probe=parse_probe(binary, probe_home),
    )

    assert problems == []


def test_the_launch_args_of_the_app_server_agent_are_accepted(probe_home: Path) -> None:
    binary, scopes = _scopes(_APP_SERVER_AGENT, probe_home)
    launch = resolve_app_server_args(_APP_SERVER_AGENT, _APP_SERVER_CONFIG, "api_key")

    problems = check_args(
        scopes,
        launch.args,
        subcommands=SUBCOMMANDS[_APP_SERVER_AGENT],
        probe=parse_probe(binary, probe_home),
    )

    assert launch.dropped == []
    assert problems == []


@pytest.mark.parametrize("backend", sorted(_TIER_FLAG))
def test_the_tier_flag_still_lists_the_values_it_takes(backend: str, probe_home: Path) -> None:
    _, scopes = _scopes(backend, probe_home)

    option = scopes[()].get(_TIER_FLAG[backend])

    assert option is not None, f"{backend} no longer prints {_TIER_FLAG[backend]}"
    assert option.takes_value and option.choices, f"{backend} no longer lists the values of {_TIER_FLAG[backend]}"


@pytest.mark.parametrize("backend", sorted(_TIER_FLAG))
def test_a_tier_value_the_cli_does_not_take_is_refused_by_the_live_judge(backend: str, probe_home: Path) -> None:
    binary, scopes = _scopes(backend, probe_home)
    args = _launch_args(backend, "safe")
    flag = args.index(_TIER_FLAG[backend])
    wrong = [*args[: flag + 1], "__not_a_tier__", *args[flag + 2 :]]

    problems = check_args(
        scopes,
        wrong,
        subcommands=SUBCOMMANDS.get(backend, frozenset()),
        probe=parse_probe(binary, probe_home),
    )

    assert problems


@pytest.mark.parametrize("backend", sorted(_TIER_FLAG))
def test_a_flag_the_cli_does_not_have_is_refused_by_the_live_judge(backend: str, probe_home: Path) -> None:
    binary, scopes = _scopes(backend, probe_home)
    args = _launch_args(backend, "safe")
    flag = args.index(_TIER_FLAG[backend])
    renamed = [*args[:flag], f"{_TIER_FLAG[backend]}-renamed", *args[flag + 1 :]]

    problems = check_args(
        scopes,
        renamed,
        subcommands=SUBCOMMANDS.get(backend, frozenset()),
        probe=parse_probe(binary, probe_home),
    )

    assert problems


def test_a_flag_the_app_server_does_not_have_is_refused_by_the_live_judge(probe_home: Path) -> None:
    binary, scopes = _scopes(_APP_SERVER_AGENT, probe_home)
    args = resolve_app_server_args(_APP_SERVER_AGENT, _APP_SERVER_CONFIG, "api_key").args

    problems = check_args(
        scopes,
        [*args, "--no-such-app-server-flag"],
        subcommands=SUBCOMMANDS[_APP_SERVER_AGENT],
        probe=parse_probe(binary, probe_home),
    )

    assert problems
