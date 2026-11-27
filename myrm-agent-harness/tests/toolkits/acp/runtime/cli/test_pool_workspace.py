"""``RuntimePool`` starts a CLI turn where the turn says, never where the host process happens to be.

The chain is real: ``RuntimePool`` -> ``CliRuntime`` -> a stand-in CLI that records the directory it was started in.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from myrm_agent_harness.core.context_vars import workspace_root_var
from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import AcpErrorCode, RuntimeConfig, RuntimeEvent

from ._fakes import FakeCli, error_codes_of, errors_of, text_of

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the CLI stand-ins are POSIX shebang scripts")

_WAIT_SECONDS = 15


@pytest.fixture
async def pool(fake_cli: FakeCli) -> AsyncIterator[RuntimePool]:
    runtime_pool = RuntimePool(max_concurrent=2)
    runtime_pool.register(
        "claude",
        RuntimeConfig(
            backend_type="cli",
            command=str(fake_cli.command("claude")),
            args=["-p", "--output-format", "stream-json"],
            permission_mode="safe",
            timeout_seconds=20,
        ),
    )
    yield runtime_pool
    await runtime_pool.close_all()


def _directory(parent: Path, name: str) -> Path:
    directory = parent / name
    directory.mkdir()
    return directory


async def _turn(pool: RuntimePool, workspace: Path, *, session_id: str = "chat-1") -> list[RuntimeEvent]:
    return [e async for e in pool.run_turn("claude", "prompt", session_id=session_id, workspace=str(workspace))]


def _started_in(fake_cli: FakeCli) -> list[str]:
    return [str(call["cwd"]) for call in fake_cli.calls()]


@pytest.mark.parametrize("ambient", ["unbound", "another-directory"])
async def test_a_turn_starts_the_cli_in_its_workspace_whatever_the_host_workspace_is(
    ambient: str, pool: RuntimePool, fake_cli: FakeCli, tmp_path: Path
) -> None:
    work = _directory(tmp_path, "work")
    token = workspace_root_var.set(str(_directory(tmp_path, "elsewhere"))) if ambient == "another-directory" else None
    try:
        events = await _turn(pool, work)
    finally:
        if token is not None:
            workspace_root_var.reset(token)

    assert not errors_of(events)
    assert _started_in(fake_cli) == [str(work.resolve())]


async def test_one_chat_in_two_workspaces_keeps_one_cli_session_per_workspace(
    pool: RuntimePool, fake_cli: FakeCli, tmp_path: Path
) -> None:
    repo_a, repo_b = _directory(tmp_path, "a"), _directory(tmp_path, "b")

    first_in_a = text_of(await _turn(pool, repo_a))
    first_in_b = text_of(await _turn(pool, repo_b))
    second_in_a = text_of(await _turn(pool, repo_a))

    assert first_in_a != first_in_b
    assert second_in_a == first_in_a
    assert fake_cli.resume_args() == [None, None, first_in_a]
    assert _started_in(fake_cli) == [str(repo_a.resolve()), str(repo_b.resolve()), str(repo_a.resolve())]


async def test_two_spellings_of_one_workspace_are_one_backend(pool: RuntimePool, tmp_path: Path) -> None:
    repo = _directory(tmp_path, "repo")

    assert pool.get("claude", f"{repo}/") is pool.get("claude", str(repo))
    assert pool.get("claude", f"{repo}/../repo") is pool.get("claude", str(repo))
    assert pool.get("claude", str(tmp_path)) is not pool.get("claude", str(repo))


async def test_a_relative_workspace_is_refused_before_the_cli_starts(pool: RuntimePool, fake_cli: FakeCli) -> None:
    with pytest.raises(ValueError, match="absolute path"):
        await _turn(pool, Path("repo"))

    assert fake_cli.calls() == []


async def test_turns_in_different_workspaces_run_side_by_side_and_each_can_be_stopped_alone(
    pool: RuntimePool, fake_cli: FakeCli, tmp_path: Path
) -> None:
    repo_a, repo_b = _directory(tmp_path, "a"), _directory(tmp_path, "b")
    fake_cli.set_scenario({"hang_before_output": True})
    turn_a = asyncio.create_task(_turn(pool, repo_a, session_id="chat-a"))
    turn_b = asyncio.create_task(_turn(pool, repo_b, session_id="chat-b"))
    async with asyncio.timeout(_WAIT_SECONDS):
        while len(fake_cli.calls()) < 2:
            await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)

    await pool.cancel("claude", "chat-a")
    stopped_a = await asyncio.wait_for(turn_a, _WAIT_SECONDS)

    assert error_codes_of(stopped_a) == [AcpErrorCode.CANCELLED]
    assert not turn_b.done()

    await pool.cancel("claude", "chat-b")
    stopped_b = await asyncio.wait_for(turn_b, _WAIT_SECONDS)

    assert error_codes_of(stopped_b) == [AcpErrorCode.CANCELLED]
