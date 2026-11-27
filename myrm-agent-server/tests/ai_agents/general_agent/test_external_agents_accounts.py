"""Which account each configured external agent runs as, and the home that puts its CLI in.

[POS]
Tests for the ``account`` entry of an external agent config in
app/ai_agents/general_agent/external_agents_runtime_config.py: it reaches the runtime as the account's home, a name no
account can have drops the agent instead of reaching a path, and a different account is a different pool.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.ai_agents.general_agent.external_agents_runtime_config import (
    _account,
    _config_fingerprint,
    _register_backends_on_pool,
)
from app.config.deploy_mode import get_deploy_mode
from app.platform_utils.deployment_capabilities import _reset_capabilities_cache_for_testing


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


def _registered(configs: list[dict[str, object]]) -> dict[str, object]:
    """The runtime config each agent was registered with, by name."""
    pool = MagicMock()
    _register_backends_on_pool(pool, configs)
    return {call.args[0]: call.args[1] for call in pool.register.call_args_list}


class TestAnAccountReachesTheRuntime:
    def test_an_agent_with_an_account_is_registered_in_its_home(self, accounts_root: Path) -> None:
        registered = _registered([{"name": "claude", "command": "claude", "account": "work"}])

        assert registered["claude"].account_home == str(accounts_root / "claude" / "work")  # type: ignore[attr-defined]
        assert (accounts_root / "claude" / "work").is_dir()

    def test_an_agent_without_an_account_is_registered_in_the_cli_own_home(self, accounts_root: Path) -> None:
        registered = _registered([{"name": "claude", "command": "claude"}, {"name": "codex", "command": "codex", "account": ""}])

        assert registered["claude"].account_home is None  # type: ignore[attr-defined]
        assert registered["codex"].account_home is None  # type: ignore[attr-defined]
        assert not accounts_root.exists()

    def test_two_agents_can_run_as_two_accounts_of_the_same_cli(self, accounts_root: Path) -> None:
        registered = _registered(
            [
                {"name": "claude-work", "command": "claude", "account": "work"},
                {"name": "claude-home", "command": "claude", "account": "home"},
            ]
        )

        assert registered["claude-work"].account_home != registered["claude-home"].account_home  # type: ignore[attr-defined]

    def test_a_sandbox_registers_every_known_agent_in_an_api_key_home(
        self, accounts_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_deploy_mode(monkeypatch, "sandbox")

        registered = _registered([{"name": "codex", "command": "codex", "account": "work"}])

        assert registered["codex"].account_home == str(accounts_root / "_api_key" / "codex")  # type: ignore[attr-defined]

    def test_a_command_that_is_not_a_known_agent_is_registered_where_it_is(self, accounts_root: Path) -> None:
        registered = _registered([{"name": "mine", "command": "/opt/bin/my-agent", "account": "work"}])

        assert registered["mine"].account_home is None  # type: ignore[attr-defined]


class TestAnAccountThatCannotBeUsed:
    @pytest.mark.parametrize("account", ["../escape", "Work", "a b", "x" * 33, 7, ["work"]])
    def test_the_agent_is_dropped_and_no_path_is_reached(
        self, accounts_root: Path, caplog: pytest.LogCaptureFixture, account: object
    ) -> None:
        with caplog.at_level(logging.WARNING):
            registered = _registered(
                [
                    {"name": "bad", "command": "claude", "account": account},
                    {"name": "good", "command": "codex"},
                ]
            )

        assert list(registered) == ["good"]
        assert "Skipping external agent 'bad': invalid account name" in caplog.text
        assert not accounts_root.exists()

    def test_a_home_that_cannot_be_made_drops_only_that_agent(
        self, accounts_root: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        accounts_root.mkdir(parents=True)
        (accounts_root / "claude").write_text("in the way", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            registered = _registered(
                [
                    {"name": "blocked", "command": "claude", "account": "work"},
                    {"name": "fine", "command": "codex", "account": "work"},
                ]
            )

        assert list(registered) == ["fine"]
        assert "Skipping external agent 'blocked': its account home is unusable" in caplog.text


class TestTheAccountIsPartOfThePoolIdentity:
    def test_another_account_is_another_pool(self) -> None:
        work = [{"name": "claude", "command": "claude", "account": "work"}]
        home = [{"name": "claude", "command": "claude", "account": "home"}]

        assert _config_fingerprint(work) != _config_fingerprint(home)
        assert _config_fingerprint(work) != _config_fingerprint([{"name": "claude", "command": "claude"}])

    def test_the_same_account_is_the_same_pool(self) -> None:
        cfg = [{"name": "claude", "command": "claude", "account": "work"}]

        assert _config_fingerprint(cfg) == _config_fingerprint([dict(cfg[0])])

    def test_no_account_and_an_empty_one_are_the_same_pool(self) -> None:
        assert _config_fingerprint([{"name": "claude", "command": "claude"}]) == _config_fingerprint(
            [{"name": "claude", "command": "claude", "account": ""}]
        )


class TestTheAccountName:
    @pytest.mark.parametrize("raw", [None, ""])
    def test_none_and_empty_mean_no_account(self, raw: object) -> None:
        assert _account({"account": raw}) is None
        assert _account({}) is None

    def test_a_valid_name_is_kept(self) -> None:
        assert _account({"account": "work-2"}) == "work-2"

    @pytest.mark.parametrize("raw", ["Work", "a/b", "..", 0, False, ["x"]])
    def test_anything_else_is_refused(self, raw: object) -> None:
        with pytest.raises(ValueError, match="invalid account name"):
            _account({"account": raw})
