"""Named accounts: one login of a CLI agent, kept in a home of its own.

Two logins of the same CLI (a work and a personal subscription) cannot share the directory the CLI keeps its login
in. An account is that directory, created by name under a root the host chooses; a CLI relocated into it (see
``DriverSpec.isolated_env``) finds only that account's login, settings and history, so accounts never mix, and the
login of the user's own default home is left alone.

[INPUT]
- toolkits.acp.drivers::DriverSpec, all_drivers, driver_for (POS: The contract of every known agent: its login, launch, transport and permission facts)

[OUTPUT]
- AccountError, AccountExistsError, AccountNotFoundError: Why an account cannot be created, found or removed.
- is_valid_account_name: Whether a name is one an account can have.
- Account: One named login of one agent and the directory that holds it.
- AccountStore: The accounts under a root directory: create, find, list, remove; and the home of agents run on an API key.

[POS]
Account registry of the ACP auth subsystem. It decides nothing about who uses an account; the host stores which agent
runs as which account and hands the account's home to the runtime (``RuntimeConfig.account_home``).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from myrm_agent_harness.toolkits.acp.drivers import DriverSpec, all_drivers, driver_for

logger = logging.getLogger(__name__)

# A name is a path component and a URL segment, so it is a lower-case slug: no separators, no dots, no case to confuse
# two accounts on a case-insensitive filesystem.
_ACCOUNT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
# Where the homes of agents run on an API key are kept: not a name an agent can have, so no account is ever there.
_API_KEY_DIR = "_api_key"


class AccountError(ValueError):
    """An account cannot be created, found or removed."""


class AccountExistsError(AccountError):
    """The agent already has an account of that name."""


class AccountNotFoundError(AccountError):
    """The agent has no account of that name."""


def is_valid_account_name(name: str) -> bool:
    """Whether ``name`` is 1-32 of ``a-z``, ``0-9``, ``_`` and ``-``, and starts with a letter or a digit."""
    return _ACCOUNT_NAME.fullmatch(name) is not None


@dataclass(frozen=True, slots=True)
class Account:
    """One named login of one agent: the directory its CLI keeps its state and its login in."""

    profile: DriverSpec
    name: str
    home: Path

    @property
    def backend(self) -> str:
        return self.profile.backend

    def env(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """``base`` (the process environment unless given) with the variables that put the CLI in this account's home.

        The environment to probe, log in or import with, so each reaches the account and not the default home.
        """
        return {**(os.environ if base is None else base), **self.profile.isolated_env(self.home)}


class AccountStore:
    """The accounts under ``root``, one directory per agent and name: ``<root>/<backend>/<name>``.

    The store is the whole registry: an account exists while its directory does, so there is nothing to keep in step
    with it. Only a directory is an account, and a link in its place is never followed.
    """

    def __init__(self, root: Path | str) -> None:
        resolved = Path(root).expanduser()
        if not resolved.is_absolute():
            msg = f"The accounts root must be an absolute path, got {str(root)!r}"
            raise ValueError(msg)
        self._root = resolved

    @property
    def root(self) -> Path:
        return self._root

    def create(self, backend: str, name: str) -> Account:
        """Make a new account with an empty home.

        Raises:
            AccountExistsError: the agent already has an account of that name.
            AccountError: ``backend`` is not a known agent or ``name`` is not a valid account name.
        """
        account = self._account(backend, name)
        _private_dir(account.home.parent)
        try:
            account.home.mkdir(mode=0o700)
        except FileExistsError as exc:
            msg = f"The {account.backend} account {name!r} already exists"
            raise AccountExistsError(msg) from exc
        _restrict(account.home)
        logger.info("account_created backend=%s name=%s", account.backend, name)
        return account

    def ensure(self, backend: str, name: str) -> Account:
        """The account, made first when the agent has none of that name.

        For a host that starts an agent as a named account: the home exists before the CLI starts, since some CLIs
        refuse a home that does not (Codex).

        Raises:
            AccountError: ``backend`` is not a known agent, ``name`` is not a valid account name, or something that
                is not a directory stands where the account's home belongs.
        """
        account = self._account(backend, name)
        if os.path.lexists(account.home) and not _is_account_dir(account.home):
            msg = f"The {account.backend} account {name!r} is not a directory"
            raise AccountError(msg)
        _private_dir(account.home.parent)
        _private_dir(account.home)
        return account

    def get(self, backend: str, name: str) -> Account:
        """The existing account.

        Raises:
            AccountNotFoundError: the agent has no account of that name.
            AccountError: ``backend`` is not a known agent or ``name`` is not a valid account name.
        """
        account = self._account(backend, name)
        if not _is_account_dir(account.home):
            msg = f"The {account.backend} account {name!r} does not exist"
            raise AccountNotFoundError(msg)
        return account

    def list(self, backend: str | None = None) -> list[Account]:
        """Every account (of one agent, when named), ordered by agent then name."""
        profiles = (self._profile(backend),) if backend is not None else all_drivers()
        found: list[Account] = []
        for profile in profiles:
            try:
                entries = sorted((self._root / profile.backend).iterdir(), key=lambda entry: entry.name)
            except (FileNotFoundError, NotADirectoryError):  # the agent has no accounts yet
                continue
            found.extend(
                Account(profile, entry.name, entry)
                for entry in entries
                if is_valid_account_name(entry.name) and _is_account_dir(entry)
            )
        return found

    def api_key_home(self, backend: str) -> Path:
        """The home an agent runs in when it is meant to bill an API key: made when missing, and no account's.

        A login left in the user's own home or in an account must not be read by a run that bills a key. No login is
        ever made to this home, so whatever the CLI would otherwise find there, it finds none.

        Raises:
            AccountError: ``backend`` is not a known agent.
        """
        home = self._root / _API_KEY_DIR / self._profile(backend).backend
        _private_dir(home.parent)
        _private_dir(home)
        return home

    def delete(self, backend: str, name: str) -> None:
        """Remove an account with everything in its home, its login included.

        Raises:
            AccountNotFoundError: the agent has no account of that name.
            AccountError: ``backend`` is not a known agent or ``name`` is not a valid account name.
        """
        account = self.get(backend, name)
        shutil.rmtree(account.home)
        logger.info("account_deleted backend=%s name=%s", account.backend, name)

    def _account(self, backend: str, name: str) -> Account:
        profile = self._profile(backend)
        if not is_valid_account_name(name):
            msg = f"An account name is 1-32 of a-z, 0-9, _ and -, and starts with a letter or digit; got {name!r}"
            raise AccountError(msg)
        return Account(profile, name, self._root / profile.backend / name)

    @staticmethod
    def _profile(backend: str) -> DriverSpec:
        profile = driver_for(backend)
        if profile is None:
            msg = f"Unknown external agent backend: {backend!r}"
            raise AccountError(msg)
        return profile


def _is_account_dir(path: Path) -> bool:
    """A real directory: a link, even one to a directory, is never an account."""
    return path.is_dir() and not path.is_symlink()


def _private_dir(path: Path) -> None:
    """Make ``path`` a directory only its owner can enter (a no-op for one that already is)."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _restrict(path)


def _restrict(path: Path) -> None:
    try:
        path.chmod(0o700)
    except OSError:
        logger.debug("account_chmod_failed path=%s", path, exc_info=True)
