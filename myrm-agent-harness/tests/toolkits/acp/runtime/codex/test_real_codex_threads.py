"""A real ``codex app-server``: conversations that continue, logins, failures, images and the host's MCP servers.

Same arrangement as ``test_real_codex_app_server``: the model is scripted (``FakeResponsesApi``), the binary and its
protocol are real. Run with ``-m integration``; a Codex that is not installed skips the tests.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.mcp_servers import StdioMcpServer
from myrm_agent_harness.toolkits.acp.types import (
    AcpErrorCode,
    CliSessionRecord,
    RuntimeEvent,
    RuntimeEventType,
)
from tests.toolkits.acp._brokers import AnsweringBroker
from tests.toolkits.acp._fake_model_apis import FakeResponsesApi, ToolCall
from tests.toolkits.acp._stores import RecordingStore
from tests.toolkits.acp.runtime.codex._real import (
    codex_pool,
    echo_mcp_server,
    ended_cleanly,
    errors_of,
    failure_of,
    run_turn,
    text_of,
    write_codex_home,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="the scripted shell command is POSIX"),
]

_UNKNOWN_THREAD = "00000000-0000-4000-8000-000000000000"
# A PNG of one pixel: Codex decodes an image before it sends it, and leaves out one it cannot.
_PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def _statuses(events: list[RuntimeEvent]) -> list[str]:
    return [str(e.data["status"]) for e in events if e.type is RuntimeEventType.STATUS_UPDATE]


async def test_a_conversation_continues_in_a_new_process_from_the_store(tmp_path: Path, workspace: Path) -> None:
    store = RecordingStore()
    async with FakeResponsesApi(final_text="noted") as api:
        env = write_codex_home(tmp_path, api.base_url)
        first = codex_pool(env, store=store)
        try:
            await run_turn(first, workspace, prompt="my secret fruit is plum")
        finally:
            await first.close_all()
        assert set(store.records) == {"chat-1"}

        second = codex_pool(env, store=store)
        try:
            events = await run_turn(second, workspace, prompt="which fruit?")
        finally:
            await second.close_all()

    assert ended_cleanly(events)
    assert not {"session_expired", "session_workspace_changed"} & set(_statuses(events))
    assert "plum" in json.dumps(api.requests[-1])


async def test_a_conversation_continues_in_the_same_process_without_a_store(tmp_path: Path, workspace: Path) -> None:
    async with FakeResponsesApi(final_text="noted") as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url))
        try:
            await run_turn(pool, workspace, prompt="my secret fruit is plum")
            events = await run_turn(pool, workspace, prompt="which fruit?")
        finally:
            await pool.close_all()

    assert ended_cleanly(events)
    assert "plum" in json.dumps(api.requests[-1])


async def test_a_thread_the_server_does_not_know_starts_a_fresh_one_and_says_so(
    tmp_path: Path, workspace: Path
) -> None:
    store = RecordingStore()
    store.records["chat-1"] = CliSessionRecord(_UNKNOWN_THREAD, str(workspace.resolve()))
    async with FakeResponsesApi(final_text="hello again") as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url), store=store)
        try:
            events = await run_turn(pool, workspace)
        finally:
            await pool.close_all()

    assert ended_cleanly(events)
    assert "session_expired" in _statuses(events)
    assert text_of(events) == "hello again"
    assert store.records["chat-1"].cli_session_id != _UNKNOWN_THREAD


async def test_a_refused_login_fails_the_turn_within_seconds_instead_of_retrying_for_minutes(
    tmp_path: Path, workspace: Path
) -> None:
    async with FakeResponsesApi(fail_status=401) as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url, retries=None))
        started = time.monotonic()
        try:
            events = await run_turn(pool, workspace)
        finally:
            await pool.close_all()
        elapsed = time.monotonic() - started

    (error,) = errors_of(events)
    assert failure_of(error).code is AcpErrorCode.AUTH_FAILED
    assert elapsed < 60
    assert len(api.requests) <= 4


async def test_a_failing_model_api_is_reported_as_a_retryable_failure(tmp_path: Path, workspace: Path) -> None:
    async with FakeResponsesApi(fail_status=500) as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url))
        try:
            events = await run_turn(pool, workspace)
        finally:
            await pool.close_all()

    (error,) = errors_of(events)
    failure = failure_of(error)
    assert failure.code is AcpErrorCode.UNKNOWN
    assert failure.retryable
    assert failure.message
    assert len(api.requests) == 1


@pytest.mark.parametrize("status", [403, 429], ids=["forbidden", "quota"])
async def test_a_failure_that_is_not_a_refused_login_does_not_send_the_user_to_sign_in(
    tmp_path: Path, workspace: Path, status: int
) -> None:
    # A 403 can be a blocked region or a plan limit and a 429 a quota: the login itself was fine.
    async with FakeResponsesApi(fail_status=status) as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url))
        try:
            events = await run_turn(pool, workspace)
        finally:
            await pool.close_all()

    (error,) = errors_of(events)
    failure = failure_of(error)
    assert failure.code is not AcpErrorCode.AUTH_FAILED
    assert failure.message.strip() not in {"", "{}"}


async def test_images_reach_the_model_as_files_not_arguments(tmp_path: Path, workspace: Path) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(_PIXEL)
    async with FakeResponsesApi(final_text="seen") as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url))
        try:
            events = await run_turn(pool, workspace, images=[str(image)])
        finally:
            await pool.close_all()

    assert ended_cleanly(events)
    sent = json.dumps(api.requests)
    assert "input_image" in sent
    assert "data:image/png;base64," in sent


async def test_an_mcp_server_of_the_host_is_offered_asked_about_and_called(tmp_path: Path, workspace: Path) -> None:
    broker = AnsweringBroker(True)
    call = ToolCall("ping", {"text": "hi"}, namespace="mcp__echo")
    async with FakeResponsesApi(calls=[call]) as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url), mcp_servers=[echo_mcp_server()])
        try:
            events = await run_turn(pool, workspace, broker=broker)
        finally:
            await pool.close_all()

    assert ended_cleanly(events)
    (request,) = broker.asked
    assert (request.tool_name, request.input) == ("mcp__echo__ping", {"text": "hi"})
    assert "pong:hi" in api.tool_results[0]
    tool_start = next(e for e in events if e.type is RuntimeEventType.TOOL_START)
    assert tool_start.data["tool_name"] == "mcp__echo__ping"


async def test_an_mcp_server_of_the_host_that_cannot_start_ends_the_turn_saying_which(
    tmp_path: Path, workspace: Path
) -> None:
    broken = StdioMcpServer(name="broken", command=str(tmp_path / "no-such-server"))
    async with FakeResponsesApi() as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url), mcp_servers=[broken])
        try:
            events = await run_turn(pool, workspace)
        finally:
            await pool.close_all()

    (error,) = errors_of(events)
    assert "broken" in failure_of(error).message
    # The agent never ran without the tools it was promised.
    assert api.requests == []


async def test_an_api_key_logs_in_through_the_server_and_leaves_the_users_own_login_alone(
    tmp_path: Path, workspace: Path
) -> None:
    async with FakeResponsesApi() as api:
        env = write_codex_home(tmp_path, api.base_url, builtin=True)
        own_login = Path(env["CODEX_HOME"]) / "auth.json"
        own_login.write_text(json.dumps({"OPENAI_API_KEY": "sk-the-users-own-key"}))
        pool = codex_pool({**env, "OPENAI_API_KEY": "sk-handed-over"})
        try:
            events = await run_turn(pool, workspace)
        finally:
            await pool.close_all()

    assert ended_cleanly(events)
    assert set(api.authorizations) == {"Bearer sk-handed-over"}
    assert json.loads(own_login.read_text()) == {"OPENAI_API_KEY": "sk-the-users-own-key"}


async def test_a_released_pool_starts_the_server_again_for_the_next_turn(tmp_path: Path, workspace: Path) -> None:
    store = RecordingStore()
    async with FakeResponsesApi(final_text="noted") as api:
        pool = codex_pool(write_codex_home(tmp_path, api.base_url), store=store)
        try:
            await run_turn(pool, workspace, prompt="my secret fruit is plum")
            await pool.release_resident()
            events = await run_turn(pool, workspace, prompt="which fruit?")
        finally:
            await pool.close_all()

    assert ended_cleanly(events)
    assert "plum" in json.dumps(api.requests[-1])
