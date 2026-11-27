"""E2E: org-pushed MCP servers are genuinely invoked by a live agent stream.

Bypasses the browser; drives the real agent stream in-process (TestClient) with
``load_user_configs`` mocked to include an ``org_mcp_dict`` entry, then asserts
the MCP tool call really happened (Goodhart guard: the prompt never contains the
tool's output tokens, so they can only come from the real tool result).

``TestOrgMCPAgent`` proves the tool is reachable; ``TestOrgMCPEnvGuard``,
``TestUserMCPEnvGuard`` and ``TestRequestMCPEnvGuard`` prove the environment the stdio
server process receives, whether the Control Plane pushed the server, the user saved it
in Settings, or the WebUI sent it with the message (``mcp_cfg``, which wins over the
saved value): code-injection variable names configured on the server are stripped, an
ordinary variable still arrives.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.api.agent.mcp_e2e_helpers import preflight_llm_check, prewarm_shared_venv
from tests.api.agent.mcp_e2e_stream import (
    McpStreamRunResult,
    print_event_sequence,
    run_mcp_agent_stream,
    skip_on_environment_errors,
)
from tests.api.agent.utils import (
    get_lite_model_selection,
    get_model_selection,
    get_search_service_config,
)
from tests.support.mcp_env_probe import ENV_PROBE_PROMPT, ENV_PROBE_SERVER, EXPECTED_ENV_REPORT, reported_env_states

_SUPPORT = Path(__file__).resolve().parents[2] / "support"

_ORG_MCP_DICT: dict[str, object] = {
    "servers": [
        {
            "id": "org-e2e-minimal",
            "name": "e2e-minimal",
            "type": "stdio",
            "command": sys.executable,
            "args": [str(_SUPPORT / "e2e_minimal_stdio_mcp_server.py")],
            "description": "org MCP probe (Control Plane pushed)",
        }
    ]
}

# The same server as the Control Plane pushes it (``orgMcpServers``), as a user saves it in Settings (``mcpServers``)
# and as the WebUI sends it with a message (``mcp_cfg``).
_ENV_PROBE_ORG_MCP_DICT: dict[str, object] = {"servers": [{**ENV_PROBE_SERVER, "id": "org-env-probe"}]}
_ENV_PROBE_USER_MCP_DICT: dict[str, object] = {"mcpConfigs": [{**ENV_PROBE_SERVER, "id": "user-env-probe", "enabled": True}]}
_ENV_PROBE_REQUEST_MCP_CFG: list[dict[str, object]] = [ENV_PROBE_SERVER]

E2E_PROMPT = (
    "请调用 e2e-minimal MCP server 提供的 ping 工具。这是一个真实存在的 MCP 工具，"
    "必须真正调用它，禁止自己编造或用代码模拟。然后把工具返回的原始结果一字不差地告诉我。"
)

_PONG_JSON = '{"result": "pong"}'


def _org_mcp_pong_delivered(result: McpStreamRunResult) -> bool:
    """True when the final answer echoes the org MCP tool's raw JSON result."""
    answer = result.full_answer
    return "pong" in answer.lower() and "result" in answer.lower()


def _mcp_tool_tasks_seen(result: McpStreamRunResult) -> list[str]:
    """List tool names seen in tasks_steps for diagnostics (Direct FC MCP tools
    do not emit step data; progress/todo steps do)."""
    names: list[str] = []
    for event in result.collected_data:
        if event.get("type") != "tasks_steps":
            continue
        tool_name = event.get("tool_name")
        if isinstance(tool_name, str) and tool_name.strip():
            names.append(tool_name)
    return names


def _run_org_mcp_turn(
    client: TestClient, prompt: str, chat_prefix: str, mcp_cfg: list[dict[str, object]] | None = None
) -> McpStreamRunResult:
    """Drive one real agent turn that must call an MCP tool and print what the stream produced.

    ``mcp_cfg`` is what the WebUI sends with the message; without it the server reads the user's saved servers.
    """
    if not preflight_llm_check():
        pytest.skip("LLM API preflight check failed (connectivity / timeout)")

    start_time = time.time()

    def log(msg: str) -> None:
        print(f"[{time.time() - start_time:.1f}s] {msg}", flush=True)

    request: dict[str, object] = {
        "messageId": str(uuid.uuid4()),
        "chatId": f"{chat_prefix}-{uuid.uuid4().hex[:8]}",
        "query": prompt,
        "action_mode": "agent",
        "modelSelection": get_model_selection(),
        "liteModelSelection": get_lite_model_selection(),
        "searchServiceCfg": get_search_service_config(),
        "enable_memory_auto_extraction": False,
        **({"mcp_cfg": mcp_cfg} if mcp_cfg else {}),
    }

    log("query: " + prompt)
    result = run_mcp_agent_stream(
        client,
        request,
        resume_on_iteration_limit=True,
        log=log,
    )
    print_event_sequence(result.event_types)
    print(f"\n=== tasks_steps tools: {_mcp_tool_tasks_seen(result)} ===")
    answer = result.full_answer
    if answer:
        print(f"\n=== FULL ANSWER ({len(answer)} chars) ===")
        print(answer[:1200])
    else:
        print("\n=== NO MESSAGE CHUNKS ===")
    skip_on_environment_errors(result)
    return result


@pytest.fixture(autouse=True)
def _mcp_e2e_local_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate local GUI mode with unrestricted code-execution network."""
    monkeypatch.setenv("DEPLOY_MODE", "local")
    import myrm_agent_harness.toolkits.code_execution.config as cfg_mod
    from myrm_agent_harness.toolkits.code_execution.config import (
        ExecutionConfig,
        NetworkConfig,
        set_execution_config,
    )

    previous = cfg_mod._execution_config_cache
    set_execution_config(
        ExecutionConfig(
            network=NetworkConfig(allow_network=True, allowed_hosts=frozenset()),
        )
    )
    yield
    cfg_mod._execution_config_cache = previous


@pytest.fixture(autouse=True)
def _org_mcp_e2e_prewarm(_mcp_e2e_local_network: None) -> None:
    """Warm shared venv before agent bash so E2E graph nodes are not spent on cold start."""
    prewarm_shared_venv()
    yield


@pytest.fixture
def org_mcp_dict() -> dict[str, object]:
    """Org MCP servers the Control Plane pushes for the running test class."""
    return _ORG_MCP_DICT


@pytest.fixture
def user_mcp_dict() -> dict[str, object] | None:
    """MCP servers the user saved in Settings for the running test class (none by default)."""
    return None


@pytest.fixture
def request_mcp_cfg() -> list[dict[str, object]] | None:
    """MCP servers the WebUI sends with the message for the running test class (none by default)."""
    return None


@pytest.fixture(autouse=True)
def _org_mcp_inject_config(
    mock_load_user_configs: object,
    org_mcp_dict: dict[str, object],
    user_mcp_dict: dict[str, object] | None,
) -> None:
    """Inject org and user MCP into the mocked user configs (mirrors real org-mcp-sync and the saved Settings value)."""
    original = mock_load_user_configs.return_value
    mock_load_user_configs.return_value = dataclasses.replace(
        original,
        mcp_dict=user_mcp_dict,
        org_mcp_dict=org_mcp_dict,
        security_config_dict={"yoloModeEnabled": True, "autoModeEnabled": True},
    )
    yield


@pytest.mark.e2e
@pytest.mark.skipif(
    not os.environ.get("BASIC_API_KEY"),
    reason="E2E test requires BASIC_API_KEY environment variable",
)
class TestOrgMCPAgent:
    @pytest.mark.timeout(300)
    def test_agent_with_org_mcp(self, client: TestClient) -> None:
        result = _run_org_mcp_turn(client, E2E_PROMPT, "test-orgmcp-chat")
        answer = result.full_answer

        if not answer:
            pytest.fail("Agent produced no final answer for the org MCP ping prompt")

        assert _org_mcp_pong_delivered(result), (
            f"org MCP tool was not genuinely invoked — expected final answer to echo "
            f"{_PONG_JSON!r}, got: {answer[:300]!r} (Goodhart guard)"
        )

        print("\norg MCP agent stream test passed")


@pytest.mark.e2e
@pytest.mark.skipif(
    not os.environ.get("BASIC_API_KEY"),
    reason="E2E test requires BASIC_API_KEY environment variable",
)
class _EnvGuardTurn:
    """One real turn against the env-probe server; a subclass supplies it from one source of MCP configuration."""

    @pytest.mark.timeout(300)
    def test_injection_env_names_never_reach_the_stdio_server(
        self, client: TestClient, request_mcp_cfg: list[dict[str, object]] | None
    ) -> None:
        result = _run_org_mcp_turn(client, ENV_PROBE_PROMPT, "test-envguard-chat", request_mcp_cfg)
        answer = result.full_answer

        probe_calls = [name for name in _mcp_tool_tasks_seen(result) if "env_presence" in name]
        reported = reported_env_states([answer])
        print(f"\n=== env-probe tool calls: {probe_calls} ===")
        print(f"=== env report echoed in the final answer: {reported} ===")

        if not answer:
            pytest.fail("Agent produced no final answer for the env-probe prompt")

        assert probe_calls, "env-probe tool was never called — no tasks_steps event names it"
        assert reported == EXPECTED_ENV_REPORT, (
            f"stdio server received a GIT_CONFIG_ variable or the tool result was fabricated — "
            f"expected {EXPECTED_ENV_REPORT}, answer reported {reported} (Goodhart guard)"
        )

        print("\nMCP env guard stream test passed")


class TestOrgMCPEnvGuard(_EnvGuardTurn):
    @pytest.fixture
    def org_mcp_dict(self) -> dict[str, object]:
        return _ENV_PROBE_ORG_MCP_DICT


class TestUserMCPEnvGuard(_EnvGuardTurn):
    @pytest.fixture
    def org_mcp_dict(self) -> dict[str, object]:
        return {"servers": []}

    @pytest.fixture
    def user_mcp_dict(self) -> dict[str, object]:
        return _ENV_PROBE_USER_MCP_DICT


class TestRequestMCPEnvGuard(_EnvGuardTurn):
    @pytest.fixture
    def org_mcp_dict(self) -> dict[str, object]:
        return {"servers": []}

    @pytest.fixture
    def request_mcp_cfg(self) -> list[dict[str, object]]:
        return _ENV_PROBE_REQUEST_MCP_CFG
