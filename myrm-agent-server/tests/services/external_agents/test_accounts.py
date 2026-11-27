"""The home a delegated CLI runs in: its account's, the API-key one of a deployment that keeps no logins, or its own.

[POS]
Tests for app/services/external_agents/accounts.py.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from myrm_agent_harness.toolkits.acp.auth import AccountError

from app.config.deploy_mode import get_deploy_mode
from app.platform_utils.deployment_capabilities import _reset_capabilities_cache_for_testing
from app.services.external_agents.accounts import account_store, agent_home


def _use_deploy_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("DEPLOY_MODE", mode)
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


@pytest.fixture(autouse=True)
def _local_deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _use_deploy_mode(monkeypatch, "local")
    yield
    get_deploy_mode.cache_clear()
    _reset_capabilities_cache_for_testing()


@pytest.fixture
def accounts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MYRM_DATA_DIR", str(tmp_path / "data"))
    return (tmp_path / "data" / "external-agents" / "accounts").resolve()


class TestTheStore:
    def test_it_lives_under_the_state_root(self, accounts_root: Path) -> None:
        assert account_store().root == accounts_root

    def test_the_root_is_absolute_even_when_the_state_root_is_given_relative(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MYRM_DATA_DIR", "relative-data")

        assert account_store().root == (tmp_path / "relative-data" / "external-agents" / "accounts").resolve()


class TestALocalDeployment:
    def test_an_agent_without_an_account_keeps_the_cli_own_home(self, accounts_root: Path) -> None:
        assert agent_home("claude", None) is None
        assert not accounts_root.exists()

    def test_an_agent_runs_in_the_home_of_its_account_made_when_missing(self, accounts_root: Path) -> None:
        home = agent_home("claude", "work")

        assert home == str(accounts_root / "claude" / "work")
        assert Path(home).is_dir()
        assert [(a.backend, a.name) for a in account_store().list()] == [("claude", "work")]

    def test_an_account_is_the_agents_own_and_not_another_agents(self, accounts_root: Path) -> None:
        assert agent_home("claude", "work") != agent_home("codex", "work")
        assert agent_home("claude", "work") != agent_home("claude", "home")

    def test_the_command_may_be_a_path(self, accounts_root: Path) -> None:
        assert agent_home("/opt/homebrew/bin/codex", "work") == str(accounts_root / "codex" / "work")

    def test_a_name_no_account_can_have_is_refused(self, accounts_root: Path) -> None:
        with pytest.raises(AccountError):
            agent_home("claude", "../escape")

        assert not accounts_root.exists()

    def test_a_command_that_is_not_a_known_agent_cannot_be_relocated(
        self, accounts_root: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            assert agent_home("/usr/local/bin/my-wrapper", "work") is None

        assert "external_agent_account_ignored command=my-wrapper" in caplog.text
        assert not accounts_root.exists()

    def test_a_command_that_is_not_a_known_agent_with_no_account_is_silent(
        self, accounts_root: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            assert agent_home("my-wrapper", None) is None

        assert caplog.text == ""


class TestADeploymentWithoutSubscriptionLogins:
    @pytest.fixture(autouse=True)
    def _sandbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _use_deploy_mode(monkeypatch, "sandbox")

    def test_every_agent_runs_in_an_api_key_home_of_its_own(self, accounts_root: Path) -> None:
        claude = agent_home("claude", None)
        codex = agent_home("codex", None)

        assert claude == str(accounts_root / "_api_key" / "claude")
        assert codex == str(accounts_root / "_api_key" / "codex")
        assert Path(claude).is_dir()

    def test_the_home_is_the_same_each_time(self, accounts_root: Path) -> None:
        assert agent_home("claude", None) == agent_home("claude", None)

    def test_a_stored_account_does_not_take_the_agent_out_of_it(self, accounts_root: Path) -> None:
        assert agent_home("claude", "work") == str(accounts_root / "_api_key" / "claude")
        assert not (accounts_root / "claude").exists()

    def test_it_is_no_account(self, accounts_root: Path) -> None:
        agent_home("claude", None)

        assert account_store().list() == []

    def test_a_command_that_is_not_a_known_agent_is_left_alone(self, accounts_root: Path) -> None:
        assert agent_home("my-wrapper", None) is None
        assert not accounts_root.exists()
