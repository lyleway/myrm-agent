"""Named accounts: one directory per login of an agent, the directory tree being the whole registry."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import (
    AccountError,
    AccountExistsError,
    AccountNotFoundError,
    AccountStore,
    CredentialStore,
    is_valid_account_name,
)
from myrm_agent_harness.toolkits.acp.drivers import driver_for, known_backends
from tests.toolkits.acp._logins import plant_login

_POSIX = sys.platform != "win32"
_BACKENDS = list(known_backends())


@pytest.fixture
def store(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "accounts")


class TestNames:
    @pytest.mark.parametrize("name", ["a", "work", "work-2", "my_account", "9", "x" * 32])
    def test_a_slug_is_a_name(self, name: str) -> None:
        assert is_valid_account_name(name)

    @pytest.mark.parametrize(
        "name",
        ["", "Work", "a b", "../x", "a/b", "a\\b", ".hidden", "-lead", "_lead", "x" * 33, "work.1", "w\u00f6rk", "a\n"],
    )
    def test_anything_else_is_not(self, name: str) -> None:
        assert not is_valid_account_name(name)

    @pytest.mark.parametrize("name", ["../escape", "a/b", "Work", ""])
    def test_a_bad_name_makes_no_directory(self, store: AccountStore, name: str) -> None:
        with pytest.raises(AccountError):
            store.create("claude", name)
        assert not store.root.exists()


class TestCreate:
    def test_a_new_account_is_an_empty_home_under_its_agent(self, store: AccountStore) -> None:
        account = store.create("claude", "work")

        assert (account.backend, account.name) == ("claude", "work")
        assert account.home == store.root / "claude" / "work"
        assert account.home.is_dir()
        assert list(account.home.iterdir()) == []

    def test_the_same_account_twice_is_refused_and_keeps_what_the_first_holds(self, store: AccountStore) -> None:
        first = store.create("codex", "work")
        (first.home / "auth.json").write_text("{}")

        with pytest.raises(AccountExistsError):
            store.create("codex", "work")
        assert (first.home / "auth.json").read_text() == "{}"

    def test_two_agents_can_each_have_an_account_of_the_same_name(self, store: AccountStore) -> None:
        assert store.create("claude", "work").home != store.create("codex", "work").home

    def test_an_alias_of_an_agent_names_the_agent(self, store: AccountStore) -> None:
        account = store.create("claude-code", "work")

        assert account.backend == "claude"
        assert account.home == store.root / "claude" / "work"

    def test_an_unknown_agent_has_no_accounts(self, store: AccountStore) -> None:
        with pytest.raises(AccountError, match="Unknown external agent backend"):
            store.create("totally-unknown-agent", "work")

    @pytest.mark.skipif(not _POSIX, reason="POSIX file permissions")
    def test_only_the_owner_can_enter_the_directories(self, store: AccountStore) -> None:
        account = store.create("claude", "work")

        assert account.home.stat().st_mode & 0o777 == 0o700
        assert account.home.parent.stat().st_mode & 0o777 == 0o700

    def test_a_relative_root_is_refused(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            AccountStore("relative/accounts")


class TestEnsure:
    def test_makes_the_account_when_there_is_none(self, store: AccountStore) -> None:
        account = store.ensure("codex", "work")

        assert account.home.is_dir()
        assert store.get("codex", "work") == account

    def test_leaves_an_existing_account_as_it_is(self, store: AccountStore) -> None:
        account = store.create("codex", "work")
        (account.home / "auth.json").write_text("{}")

        assert store.ensure("codex", "work") == account
        assert (account.home / "auth.json").read_text() == "{}"

    def test_refuses_a_file_where_the_home_belongs(self, store: AccountStore) -> None:
        (store.root / "codex").mkdir(parents=True)
        (store.root / "codex" / "work").write_text("not a directory")

        with pytest.raises(AccountError, match="not a directory"):
            store.ensure("codex", "work")


class TestFindAndList:
    def test_get_finds_an_existing_account_and_not_a_missing_one(self, store: AccountStore) -> None:
        created = store.create("claude", "work")

        assert store.get("claude", "work") == created
        with pytest.raises(AccountNotFoundError):
            store.get("claude", "other")

    def test_the_directory_tree_is_the_registry(self, store: AccountStore) -> None:
        created = store.create("claude", "work")

        assert AccountStore(store.root).get("claude", "work") == created
        assert AccountStore(store.root).list() == [created]

    def test_lists_by_agent_then_name(self, store: AccountStore) -> None:
        for backend, name in [("qwen", "b"), ("claude", "work"), ("claude", "home"), ("codex", "a")]:
            store.create(backend, name)

        assert [(a.backend, a.name) for a in store.list()] == [
            ("claude", "home"),
            ("claude", "work"),
            ("codex", "a"),
            ("qwen", "b"),
        ]
        assert [a.name for a in store.list("claude")] == ["home", "work"]
        assert store.list("gemini") == []

    def test_an_empty_root_lists_nothing(self, store: AccountStore) -> None:
        assert store.list() == []

    def test_what_is_not_an_account_is_not_listed(self, store: AccountStore, tmp_path: Path) -> None:
        store.create("claude", "work")
        (store.root / "claude" / "a-file").write_text("x")
        (store.root / "claude" / "Bad Name").mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (store.root / "claude" / "linked").symlink_to(elsewhere, target_is_directory=True)

        assert [a.name for a in store.list("claude")] == ["work"]
        with pytest.raises(AccountNotFoundError):
            store.get("claude", "linked")
        with pytest.raises(AccountError, match="not a directory"):
            store.ensure("claude", "linked")


class TestDelete:
    def test_removes_the_home_with_everything_in_it(self, store: AccountStore) -> None:
        account = store.create("claude", "work")
        (account.home / "nested").mkdir()
        (account.home / "nested" / ".credentials.json").write_text("{}")

        store.delete("claude", "work")

        assert not account.home.exists()
        assert store.list() == []

    def test_a_link_inside_a_home_is_removed_and_its_target_is_not(self, store: AccountStore, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        account = store.create("claude", "work")
        (account.home / "link").symlink_to(outside, target_is_directory=True)

        store.delete("claude", "work")

        assert (outside / "keep.txt").read_text() == "keep"

    def test_a_missing_account_cannot_be_removed(self, store: AccountStore) -> None:
        with pytest.raises(AccountNotFoundError):
            store.delete("claude", "work")

    def test_a_link_in_place_of_an_account_is_never_followed(self, store: AccountStore, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.txt").write_text("keep")
        (store.root / "claude").mkdir(parents=True)
        (store.root / "claude" / "linked").symlink_to(elsewhere, target_is_directory=True)

        with pytest.raises(AccountNotFoundError):
            store.delete("claude", "linked")
        assert (elsewhere / "keep.txt").read_text() == "keep"


class TestTheCliIsRelocatedIntoTheAccount:
    """Each agent's own variables put its login, settings and history in the account's home and nowhere else."""

    @pytest.mark.parametrize("backend", _BACKENDS)
    def test_the_home_the_cli_resolves_is_inside_the_account(self, store: AccountStore, backend: str) -> None:
        account = store.create(backend, "work")

        resolved = account.profile.resolve_home(account.env({"HOME": "/home/u"}))

        assert resolved == account.home or resolved.parent == account.home

    @pytest.mark.parametrize("backend", _BACKENDS)
    def test_the_environment_it_builds_keeps_what_it_was_given(self, store: AccountStore, backend: str) -> None:
        account = store.create(backend, "work")

        env = account.env({"HOME": "/home/u", "PATH": "/usr/bin"})

        assert env["HOME"] == "/home/u"
        assert env["PATH"] == "/usr/bin"

    @pytest.mark.parametrize("backend", _BACKENDS)
    def test_a_credential_stored_for_one_account_is_seen_by_that_account_only(
        self, store: AccountStore, tmp_path: Path, backend: str
    ) -> None:
        work = store.create(backend, "work")
        personal = store.create(backend, "personal")
        default_home = {"HOME": str(tmp_path / "home")}

        plant_login(work.env(default_home), backend)

        assert CredentialStore(work.env(default_home)).is_authenticated(backend)
        assert not CredentialStore(personal.env(default_home)).is_authenticated(backend)
        assert not CredentialStore(default_home).is_authenticated(backend)
        assert any(path.is_file() for path in work.home.rglob("*"))
        assert not any(personal.home.iterdir())
        assert not (tmp_path / "home").exists()


class TestApiKeyHome:
    def test_is_a_stable_home_of_its_own_for_each_agent(self, store: AccountStore) -> None:
        home = store.api_key_home("claude")

        assert home.is_dir()
        assert store.api_key_home("claude") == home
        assert store.api_key_home("codex") != home
        assert home.is_relative_to(store.root)

    def test_is_nobodys_account_and_stays_out_of_the_listing(self, store: AccountStore) -> None:
        account = store.create("claude", "work")
        home = store.api_key_home("claude")

        assert store.list() == [account]
        assert home != account.home
        assert not home.is_relative_to(account.home.parent)

    def test_holds_no_login_the_agents_home_or_an_account_has(self, store: AccountStore, tmp_path: Path) -> None:
        default_home = {"HOME": str(tmp_path / "home")}
        plant_login(store.create("claude", "work").env(default_home), "claude")

        env = {**default_home, **driver_for("claude").isolated_env(store.api_key_home("claude"))}  # type: ignore[union-attr]

        assert not CredentialStore(env).is_authenticated("claude")

    @pytest.mark.skipif(not _POSIX, reason="POSIX file permissions")
    def test_only_the_owner_can_enter_it(self, store: AccountStore) -> None:
        home = store.api_key_home("claude")

        assert home.stat().st_mode & 0o777 == 0o700
        assert home.parent.stat().st_mode & 0o777 == 0o700

    def test_an_unknown_agent_has_none(self, store: AccountStore) -> None:
        with pytest.raises(AccountError, match="Unknown external agent backend"):
            store.api_key_home("totally-unknown-agent")
