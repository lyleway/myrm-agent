"""Chrome E2E: org-managed MCP servers invoked during a live agent chat.

[INPUT]
- Control Plane pushes an org-level stdio MCP server via ``POST /api/admin/org-mcp-sync``.
- WebUI chat sends a message that requires calling the org MCP server's tool.

[OUTPUT]
- ``test_org_mcp_tool_invoked_in_live_chat``: the assistant turn completes and the tool result (``pong``) surfaces in the
  persisted chat messages *and* in the rendered chat UI — proving org MCPs are wired into the live agent path end to end
  (sync → merge → agent toolset → tool call → UI result).
- ``test_org_mcp_server_never_receives_code_injection_env_in_live_chat``: a pushed server whose stored environment names
  ``GIT_CONFIG_*`` is started without them while its ordinary variable arrives; the stored configuration is read back first,
  so the names really were in the store when the server was started.
- The persisted turn and the visible UI tail are printed (``E2E_LIVE_TURN_*``)
  so a PASS carries proof of what the model actually returned.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cdp_chat.mcp_ui import McpChatSession  # noqa: E402
from cdp_chat.support import (  # noqa: E402
    ensure_e2e_yolo_mode,
    fetch_chat_messages,
    get_e2e_api_url,
    get_e2e_ui_url,
)
from cdp_chat.ui import chat_id_from_path, wait_e2e_provider_ready  # noqa: E402
from chrome_mcp.client import ChromeMcpClient, McpPage  # noqa: E402
from dev_gate.contract import EvaluateIntent  # noqa: E402

from tests.support.chrome_mcp_e2e import http_json  # noqa: E402
from tests.support.e2e_runtime_guard import E2EResourceLedger, heartbeat_once  # noqa: E402
from tests.support.mcp_env_probe import (  # noqa: E402
    ENV_PROBE_ENV,
    ENV_PROBE_PROMPT,
    ENV_PROBE_SERVER,
    EXPECTED_ENV_REPORT,
    reported_env_states,
)

_ORG_PROBE_NAME = "e2e-minimal"
_ORG_MCP_SYNC_PATH = "/api/admin/org-mcp-sync"
_STDIO_STUB = Path(__file__).resolve().parents[1] / "support" / "e2e_minimal_stdio_mcp_server.py"

E2E_PROMPT = (
    "请调用 e2e-minimal MCP server 提供的 ping 工具。这是一个真实存在的 MCP 工具，"
    "必须真正调用它，禁止自己编造或用代码模拟。然后把工具返回的原始结果一字不差地告诉我，"
    "最后回复 OK。"
)
TURN_WAIT_SEC = 120.0
_EVIDENCE_CONTENT_CHARS = 400
_EVIDENCE_METADATA_CHARS = 800
# The prompt never contains "pong", so a match can only come from the tool result or the model's reply.
_VISIBLE_PONG_JS = """(() => {
  const text = document.body?.innerText || '';
  return { hasPong: /pong/i.test(text), tail: text.slice(-400) };
})()"""


def _sync_org_mcp(api_url: str, servers: list[dict[str, object]]) -> None:
    resp = http_json(
        "POST",
        f"{api_url}{_ORG_MCP_SYNC_PATH}",
        {"mcp_servers": servers},
    )
    assert isinstance(resp, dict) and resp.get("status") == "synced", resp


def _org_mcp_servers(api_url: str) -> list[dict[str, object]]:
    record = http_json("GET", f"{api_url}/api/v1/config/orgMcpServers")
    assert isinstance(record, dict), record
    value = record.get("value") or {}
    servers = value.get("servers") or []
    assert isinstance(servers, list), servers
    return [s for s in servers if isinstance(s, dict)]


def _org_mcp_server_names(api_url: str) -> list[str]:
    return [str(s.get("name", "")) for s in _org_mcp_servers(api_url)]


def _stored_env_names(server: dict[str, object]) -> set[str]:
    """Names of the environment variables a stored server entry carries."""
    extra_params = server.get("extra_params")
    env = extra_params.get("env") if isinstance(extra_params, dict) else None
    return {str(name) for name in env} if isinstance(env, dict) else set()


def _message_text(msg: dict[str, object]) -> str:
    """The text of a persisted message, whether its content is one string or a list of chunks."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for chunk in content:
        if isinstance(chunk, str):
            parts.append(chunk)
        elif isinstance(chunk, dict):
            text_chunk = chunk.get("text")
            if isinstance(text_chunk, str):
                parts.append(text_chunk)
    return "".join(parts)


def _pong_evidence(messages: list[dict[str, object]]) -> str:
    """Return the first ``pong`` evidence in tool results or assistant text."""
    for msg in messages:
        text = _message_text(msg)
        if "pong" in text.lower():
            return f"{msg.get('role') or ''}:{text[:120]}"
    return ""


def _called_tool_names(messages: list[dict[str, object]]) -> list[str]:
    """Names of the tools the turn called, from the progress steps persisted with the assistant message."""
    names: list[str] = []
    for msg in messages:
        metadata = msg.get("metadata")
        steps = metadata.get("progressSteps") if isinstance(metadata, dict) else None
        for step in steps if isinstance(steps, list) else []:
            tool_name = step.get("tool_name") if isinstance(step, dict) else None
            if isinstance(tool_name, str):
                names.append(tool_name)
    return names


def _log_turn_evidence(chat_id: str, messages: list[dict[str, object]]) -> None:
    """Print every persisted message so a PASS shows what the model actually returned."""
    print(f"E2E_LIVE_TURN_EVIDENCE: chat={chat_id} messages={len(messages)}", flush=True)
    for index, msg in enumerate(messages):
        raw = msg.get("content")
        content = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
        metadata = json.dumps(msg.get("metadata") or {}, ensure_ascii=False, default=str)
        print(
            f"E2E_LIVE_TURN_EVIDENCE: #{index} role={msg.get('role')} "
            f"content={content[:_EVIDENCE_CONTENT_CHARS]!r} metadata={metadata[:_EVIDENCE_METADATA_CHARS]}",
            flush=True,
        )


def _extract_chat_id(url: str) -> str | None:
    from urllib.parse import urlparse

    return chat_id_from_path(urlparse(url).path)


async def _resolve_chat_id(
    chat: McpChatSession,
    state: dict[str, object],
) -> str | None:
    chat_id = _extract_chat_id(str(state.get("url") or ""))
    if not chat_id:
        chat_id = str(state.get("chatId") or "").strip() or None
    if chat_id:
        return chat_id
    base = get_e2e_ui_url().rstrip("/")
    href = await chat.evaluate(
        f"""(() => {{
          const base = {json.dumps(base)};
          const links = Array.from(document.querySelectorAll('aside a[href]'))
            .map((anchor) => anchor.href)
            .filter((url) => url.startsWith(base) && !url.endsWith('/') && !url.includes('/settings'));
          return links[0] || location.href;
        }})()""",
        intent=EvaluateIntent.SYNC_PROBE,
    )
    return _extract_chat_id(str(href) if href else "")


@dataclass(frozen=True)
class LiveTurn:
    """A finished turn: the chat page still open, the chat's id and the messages it persisted."""

    chat: McpChatSession
    chat_id: str
    messages: list[dict[str, object]]


@asynccontextmanager
async def _live_turn(
    api_url: str, ledger: E2EResourceLedger, prompt: str, *, reply_ends_with_ok: bool
) -> AsyncIterator[LiveTurn]:
    """Send ``prompt`` in a new WebUI chat on the private runtime and hand over the finished turn, page still open.

    ``reply_ends_with_ok`` says the prompt asks for a closing OK, which then marks the turn as done; without it the turn
    is done once the reply has stopped streaming.
    """
    if not wait_e2e_provider_ready():
        pytest.fail(
            "Provider config not ready for live E2E — run via ./myrm-dev test -m chrome_e2e "
            "after ./myrm-dev ready --chrome (API /api/v1/config/readiness provider.is_ready must be true)"
        )
    ensure_e2e_yolo_mode(api_url=api_url)
    client = ChromeMcpClient(request_timeout_sec=180.0)
    await asyncio.to_thread(client.start)
    try:
        ui_url = get_e2e_ui_url().rstrip("/")
        page: McpPage | None = await asyncio.to_thread(client.new_page, ui_url, timeout_ms=120_000)
        if page is None:
            raise RuntimeError("new_page returned no page")

        chat = McpChatSession(client, page)
        await chat.bootstrap(ui_url, navigate=False, timeout_sec=180.0)
        await chat.click_new_chat()
        heartbeat_once()

        first_send = await chat.send_message(prompt, prompt)
        chat_id_hint = (
            str(first_send.get("started", {}).get("chatId") or first_send.get("submit", {}).get("chatId") or "").strip() or None
        )

        if reply_ends_with_ok:
            after = await chat.wait_turn_done(prompt, chat_id_hint=chat_id_hint, timeout_sec=TURN_WAIT_SEC)
        else:
            after = await chat.wait_turn_settled(chat_id_hint=chat_id_hint, timeout_sec=TURN_WAIT_SEC)
        if str(after.get("path", "")).startswith("/settings"):
            pytest.fail(f"Send redirected to settings: {after}")

        chat_id = await _resolve_chat_id(chat, after)
        assert chat_id, f"Expected chat id after turn: {after}"
        ledger.register("chat", chat_id)

        messages = fetch_chat_messages(chat_id, api_url=api_url)
        _log_turn_evidence(chat_id, messages)
        yield LiveTurn(chat=chat, chat_id=chat_id, messages=messages)
    finally:
        await asyncio.to_thread(client.close)


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.asyncio
async def test_org_mcp_tool_invoked_in_live_chat(
    e2e_resource_ledger: E2EResourceLedger,
) -> None:
    """Org-pushed MCP server's tool is actually called and its result surfaces."""
    api_url = get_e2e_api_url()
    probe = {
        "name": _ORG_PROBE_NAME,
        "type": "stdio",
        "command": sys.executable,
        "args": [str(_STDIO_STUB)],
        "description": "E2E org-managed MCP probe",
    }
    _sync_org_mcp(api_url, [probe])
    try:
        assert _org_mcp_server_names(api_url) == [_ORG_PROBE_NAME], "org MCP config not visible on the private E2E backend"
        async with _live_turn(api_url, e2e_resource_ledger, E2E_PROMPT, reply_ends_with_ok=True) as turn:
            evidence = _pong_evidence(turn.messages)
            assert evidence, (
                f"Expected org MCP tool result 'pong' in chat {turn.chat_id} messages; "
                f"got {json.dumps(turn.messages, ensure_ascii=False)[:2000]}"
            )
            visible = await turn.chat.evaluate(_VISIBLE_PONG_JS, intent=EvaluateIntent.SYNC_PROBE)
            print(f"E2E_LIVE_TURN_UI: {json.dumps(visible, ensure_ascii=False)}", flush=True)
            assert isinstance(visible, dict) and visible.get("hasPong"), (
                f"Persisted 'pong' evidence {evidence!r} is not visible in the chat UI for chat {turn.chat_id}: {visible}"
            )
    finally:
        _sync_org_mcp(api_url, [])


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
@pytest.mark.asyncio
async def test_org_mcp_server_never_receives_code_injection_env_in_live_chat(
    e2e_resource_ledger: E2EResourceLedger,
) -> None:
    """A pushed server whose stored environment names ``GIT_CONFIG_*`` starts without them; its ordinary variable arrives."""
    api_url = get_e2e_api_url()
    _sync_org_mcp(api_url, [ENV_PROBE_SERVER])
    try:
        stored = _org_mcp_servers(api_url)
        assert [s.get("name") for s in stored] == [ENV_PROBE_SERVER["name"]], stored
        assert _stored_env_names(stored[0]) == set(ENV_PROBE_ENV), (
            f"the injection names must be in the store, so that it is the server start that drops them: {stored}"
        )
        async with _live_turn(api_url, e2e_resource_ledger, ENV_PROBE_PROMPT, reply_ends_with_ok=False) as turn:
            tools = _called_tool_names(turn.messages)
            assert any("env_presence" in name for name in tools), (
                f"the env-probe tool was never called; progress steps name {tools}"
            )
            answer = "\n".join(_message_text(m) for m in turn.messages if m.get("role") == "assistant")
            reported = reported_env_states([answer])
            print(f"E2E_ENV_REPORT: {reported}", flush=True)
            assert reported == EXPECTED_ENV_REPORT, (
                f"the stdio server received a GIT_CONFIG_ variable, or the tool result was not relayed — expected "
                f"{EXPECTED_ENV_REPORT}, the answer reported {reported}: {answer[:600]!r}"
            )
    finally:
        _sync_org_mcp(api_url, [])
