#!/usr/bin/env python3
"""Stdio MCP server that reports which probe environment variables its own process received."""

import os

from mcp.server import MCPServer

# Only these names are ever looked at, so the server cannot leak anything else about the host environment. The report counts
# them instead of printing ``NAME=state`` pairs: the tool-output redaction masks the value after a name like ``GIT_CONFIG_KEY_0``.
_GIT_CONFIG_NAMES = ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")
_CONTROL_NAME = "E2E_PROBE_CONTROL"

server = MCPServer(name="env-probe", version="1.0.0")


def env_presence() -> str:
    """Report how many of the probe variables are present in this server process."""
    received = [name for name in _GIT_CONFIG_NAMES if name in os.environ]
    return (
        f"git_config_vars={len(received)} control_vars={int(_CONTROL_NAME in os.environ)} received={','.join(received) or 'none'}"
    )


server.add_tool(env_presence, name="env_presence", description="Report which probe environment variables this process has")


if __name__ == "__main__":
    server.run(transport="stdio")
