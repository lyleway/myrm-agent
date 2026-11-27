"""A stdio MCP server with one tool, ``ping``, for tests that hand an agent an MCP server of the host's.

Run as a subprocess: ``python tests/toolkits/acp/_echo_mcp_server.py``. ``ping`` answers ``pong:<text>``.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer("echo")


@server.tool()
def ping(text: str) -> str:
    """Echo the text back."""
    return f"pong:{text}"


if __name__ == "__main__":
    server.run("stdio")
