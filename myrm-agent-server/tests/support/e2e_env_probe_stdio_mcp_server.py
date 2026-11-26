#!/usr/bin/env python3
"""Stdio MCP server that reports which probe environment variables its own process received."""

import os

from mcp.server import MCPServer

# Only these names are ever reported, so the server cannot leak anything else about the host environment.
_PROBED = ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "E2E_PROBE_CONTROL")

server = MCPServer(name="env-probe", version="1.0.0")


def env_presence() -> str:
    """Report whether each probe variable is present in this server process."""
    return " ".join(f"{name}={'present' if name in os.environ else 'absent'}" for name in _PROBED)


server.add_tool(env_presence, name="env_presence", description="Report which probe environment variables this process has")


if __name__ == "__main__":
    server.run(transport="stdio")
