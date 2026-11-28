"""Each real CLI, run as a named account, keeps all it writes in the account's home and sees no other account's login.

The model is scripted (``FakeAnthropicApi``, ``FakeResponsesApi``, ``FakeGeminiApi``, ``FakeOpenAiChatApi``) and the
process's own home is an empty directory that must stay empty, so a release of a CLI that stops honouring the variable
the harness relocates it with, or starts keeping its login somewhere the variable does not reach, fails here and not on
a user's machine with two subscriptions.

Run with ``-m integration``. A CLI that is not installed skips its tests, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names it.
No login and no real model are involved.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.auth import Account, AccountStore, LoginReport, probe_login
from myrm_agent_harness.toolkits.acp.auth.launch_env import cli_base_env
from myrm_agent_harness.toolkits.acp.drivers import known_backends
from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime
from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    CliSessionStore,
    ContinuityStatus,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
)
from tests.toolkits.acp._fake_anthropic import FakeAnthropicApi
from tests.toolkits.acp._fake_model_apis import FakeResponsesApi
from tests.toolkits.acp._logins import plant_login
from tests.toolkits.acp.cli_contract import CLAUDE_OAUTH, CODEX_API_KEY, cli_binary
from tests.toolkits.acp.runtime.acp._fakes import MemorySessionStore
from tests.toolkits.acp.runtime.acp._real_agents import SCRIPTED_AGENTS, scripted_pool
from tests.toolkits.acp.runtime.codex._real import STUB_KEY, codex_config_toml

pytestmark = pytest.mark.integration

_REPLY = "hello from the scripted model"
_TURN_SECONDS = 90
# What a CLI keeps in a home of the user's when nothing relocates it.
_CLI_HOMES = (".claude", ".claude.json", ".codex", ".gemini", ".qwen")

# One turn of an agent run as an account: (the account, the process's own home, the workspace, the session store).
TurnAs = Callable[[Account, Path, Path, CliSessionStore | None], Awaitable[list[RuntimeEvent]]]


async def _claude(
    account: Account, process_home: Path, workspace: Path, store: CliSessionStore | None
) -> list[RuntimeEvent]:
    async with FakeAnthropicApi("NoSuchTool", {}, final_text=_REPLY) as api:
        config = RuntimeConfig(
            backend_type="cli",
            command=cli_binary("claude"),
            auth_mode="api_key",
            env={
                "ANTHROPIC_API_KEY": "sk-ant-api03-fake",
                "ANTHROPIC_BASE_URL": api.base_url,
                "HOME": str(process_home),
                "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            timeout_seconds=_TURN_SECONDS,
            account_home=str(account.home),
        )
        runtime = CliRuntime("claude", config, workspace=str(workspace), session_store=store)
        return [event async for event in runtime.run_turn("hello", "chat-1")]


async def _codex(
    account: Account, process_home: Path, workspace: Path, store: CliSessionStore | None
) -> list[RuntimeEvent]:
    async with FakeResponsesApi(final_text=_REPLY) as api:
        # Codex finds its model API in the config.toml of its home, which for this agent is the account's.
        (account.home / "config.toml").write_text(codex_config_toml(api.base_url))
        pool = RuntimePool(max_concurrent=1, cli_session_store=store)
        pool.register(
            "codex",
            RuntimeConfig(
                backend_type="cli",
                command=cli_binary("codex"),
                auth_mode="api_key",
                env={"HOME": str(process_home), "STUB_KEY": STUB_KEY},
                timeout_seconds=_TURN_SECONDS,
                account_home=str(account.home),
            ),
        )
        try:
            return [event async for event in pool.run_turn("codex", "hello", "chat-1", workspace=str(workspace))]
        finally:
            await pool.close_all()


def _acp(agent: str) -> TurnAs:
    async def turn(
        account: Account, process_home: Path, workspace: Path, store: CliSessionStore | None
    ) -> list[RuntimeEvent]:
        async with SCRIPTED_AGENTS[agent].api(final_text=_REPLY) as api:
            pool = scripted_pool(agent, api, process_home, session_store=store, account_home=account.home)
            try:
                return [event async for event in pool.run_turn(agent, "hello", "chat-1", workspace=str(workspace))]
            finally:
                await pool.close_all()

    return turn


_TURNS: dict[str, TurnAs] = {"claude": _claude, "codex": _codex, "gemini": _acp("gemini"), "qwen": _acp("qwen")}


def _text(events: list[RuntimeEvent]) -> str:
    return "".join(str(e.data["content"]) for e in events if e.type is RuntimeEventType.TEXT_DELTA)


def _errors(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
    return [e for e in events if e.type is RuntimeEventType.ERROR]


def _notices(events: list[RuntimeEvent]) -> list[ContinuityStatus]:
    notices = (ContinuityStatus.of(e.data.get("status")) for e in events if e.type is RuntimeEventType.STATUS_UPDATE)
    return [notice for notice in notices if notice is not None]


def _files(home: Path) -> set[Path]:
    return {path for path in home.rglob("*") if path.is_file()}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def process_home(tmp_path: Path) -> Path:
    path = tmp_path / "process-home"
    path.mkdir()
    return path


@pytest.fixture
def accounts(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "accounts")


def test_every_known_agent_has_a_scripted_turn() -> None:
    assert set(_TURNS) == set(known_backends())


@pytest.mark.parametrize("agent", sorted(_TURNS))
async def test_a_turn_as_an_account_writes_only_in_the_accounts_home(
    agent: str, accounts: AccountStore, workspace: Path, process_home: Path
) -> None:
    account = accounts.create(agent, "work")
    before = _files(account.home)

    events = await _TURNS[agent](account, process_home, workspace, None)

    assert _errors(events) == []
    assert _text(events) == _REPLY
    assert _files(account.home) - before, "the CLI kept nothing in the home of its account"
    assert [name for name in _CLI_HOMES if (process_home / name).exists()] == []


@pytest.mark.parametrize("agent", sorted(_TURNS))
async def test_the_conversation_of_an_account_continues_in_that_account_only(
    agent: str, accounts: AccountStore, workspace: Path, process_home: Path
) -> None:
    store = MemorySessionStore()
    work = accounts.create(agent, "work")
    personal = accounts.create(agent, "personal")

    first = await _TURNS[agent](work, process_home, workspace, store)
    again = await _TURNS[agent](work, process_home, workspace, store)
    elsewhere = await _TURNS[agent](personal, process_home, workspace, store)

    assert _errors(first) == _errors(again) == []
    assert _notices(first) == _notices(again) == []
    assert _errors(elsewhere) == []
    # The other account has no such conversation: it gets a new one, and is told so.
    assert _notices(elsewhere) == [ContinuityStatus.SESSION_EXPIRED]
    assert _text(elsewhere) == _REPLY


async def _login_report(account: Account, process_home: Path) -> LoginReport | None:
    binary = cli_binary(account.backend)
    env = account.env({**os.environ, "HOME": str(process_home)})
    return await probe_login(account.backend, executable=binary, env=cli_base_env(binary, env), timeout_seconds=60)


def _store_login(account: Account) -> None:
    content = CLAUDE_OAUTH if account.backend == "claude" else CODEX_API_KEY
    plant_login(account.env({"HOME": str(account.home)}), account.backend, json.dumps(content))


@pytest.mark.parametrize("agent", ["claude", "codex"])
async def test_the_login_one_account_holds_is_the_login_its_cli_reports_for_that_account_alone(
    agent: str, accounts: AccountStore, process_home: Path
) -> None:
    work = accounts.create(agent, "work")
    personal = accounts.create(agent, "personal")
    _store_login(work)

    reported = await _login_report(work, process_home)
    assert reported is not None
    assert reported.logged_in
    assert await _login_report(personal, process_home) == LoginReport(False)
