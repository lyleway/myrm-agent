"""A lost authorization names the server by the name its owner gave it, whichever way it is found out.

The model calls ``mcp__{identity}__{tool}``, and the identity of a server such as ``天气`` is ``unnamed_…``: nothing a
person recognises. The text the model relays after a 401 and the notice behind the re-authorization toast are for the
user, who knows the server by its configured name. The toast also keys its de-duplication on that name, so the tool-call
path and the session path must name one server alike.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from langchain_core.tools import BaseTool, StructuredTool

from myrm_agent_harness.toolkits.mcp.agent import MCPAgent
from myrm_agent_harness.toolkits.mcp.errors import MCPToolFailure
from myrm_agent_harness.toolkits.mcp.server_identity import mcp_server_component
from myrm_agent_harness.toolkits.mcp.session_actor import MCPSessionActor

_NOTIFY = "myrm_agent_harness.toolkits.mcp.auth_notify.notify_mcp_auth_expired"

# Servers whose identity is not their name (a non-ASCII name, a space, a name past the identity bound), and one whose is.
_SERVERS = (
    "天气",
    "Hello World",
    "my-very-long-server-name-for-the-weather-service-v2",
    "weather",
)


def _unauthorized() -> Exception:
    response = MagicMock()
    response.status_code = 401
    return httpx2.HTTPStatusError("401 Unauthorized", request=MagicMock(), response=response)


def _processed_tool(server: str) -> BaseTool:
    """A tool of *server* as the pipeline hands it to the model: prefixed and wrapped, and the server answers 401."""
    raw = StructuredTool(
        name="get_forecast",
        description="Forecast",
        args_schema={"type": "object", "properties": {"city": {"type": "string"}}},
        coroutine=AsyncMock(side_effect=_unauthorized()),
    )
    [tool] = MCPAgent.process_session_tools([raw], server, None, None, 5.0)
    return tool


class TestAToolCallThatIsRefused:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("server", _SERVERS)
    async def test_the_notice_names_the_server_the_user_gave(self, server: str) -> None:
        tool = _processed_tool(server)

        with patch(_NOTIFY) as notify:
            await tool.ainvoke({"city": "Paris"})

        notify.assert_called_once()
        assert notify.call_args.args[0] == server

    @pytest.mark.asyncio
    @pytest.mark.parametrize("server", _SERVERS)
    async def test_the_text_for_the_model_names_the_server_the_user_gave(self, server: str) -> None:
        tool = _processed_tool(server)
        identity = mcp_server_component(server)

        with patch(_NOTIFY):
            result = await tool.ainvoke({"city": "Paris"})

        assert tool.name.startswith(f"mcp__{identity}__")
        assert isinstance(result, MCPToolFailure)
        assert result.startswith(f"MCP server '{server}' requires re-authorization.")
        if identity != server:
            assert identity not in result


class TestOneServerTwoWaysOfFindingOut:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("server", _SERVERS)
    async def test_the_tool_call_and_the_session_send_the_same_name(self, server: str) -> None:
        tool = _processed_tool(server)
        actor = MCPSessionActor(server, {"transport": "sse"})

        with patch(_NOTIFY) as notify:
            actor._maybe_emit_auth_expired("HTTPStatusError: 401 Unauthorized")
            await tool.ainvoke({"city": "Paris"})

        assert [call.args[0] for call in notify.call_args_list] == [server, server]
