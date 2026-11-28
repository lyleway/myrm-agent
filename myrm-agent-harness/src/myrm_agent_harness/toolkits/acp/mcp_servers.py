"""The MCP servers a host hands a delegated agent: a program the agent starts, or an endpoint it connects to.

[INPUT]
no - Base type definition module

[OUTPUT]
- StdioMcpServer: a server the agent starts itself, as a program with arguments and an environment
- HttpMcpServer: a server the agent connects to over streamable HTTP, with the headers (a credential among them) it connects with
- McpServerConfig: either of the two; what a runtime or a turn is given
- read_only_names: the names of the servers whose tools only read

[POS]
What a host can offer a delegated agent beyond the agent's own tools. Each runtime puts a server in the form its agent
takes: an ACP session carries it (an HTTP one only to an agent that advertised ``mcpCapabilities.http``), Claude Code
reads it from a ``--mcp-config`` file, Codex from the config of a thread. A server declared ``read_only`` has tools that
only read, which a ``safe`` agent may run without asking, as it reads files without asking.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# The name is a key in every agent's config and part of the tool names it gives the model (``mcp__<name>__<tool>``),
# so it stays in the characters all of them take, and no ``__`` can hide inside it: the end of the name would not be
# where the tool name starts.
_NAME = re.compile(r"(?=.{1,64}$)[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*")


def _checked_name(name: str) -> None:
    if not _NAME.fullmatch(name):
        msg = f"An MCP server name is 1 to 64 letters and digits, joined by single '-' or '_': got {name!r}"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class StdioMcpServer:
    """A server the agent starts as a program of its own."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    # ``repr`` hides what may hold a credential, so a logged config does not.
    env: dict[str, str] | None = field(default=None, repr=False)
    read_only: bool = False

    def __post_init__(self) -> None:
        _checked_name(self.name)
        if not self.command:
            msg = "A stdio MCP server needs a command"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class HttpMcpServer:
    """A server the agent connects to at ``url`` over streamable HTTP, carrying ``headers`` on every request."""

    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    read_only: bool = False

    def __post_init__(self) -> None:
        _checked_name(self.name)
        parts = urlsplit(self.url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            # The url is not echoed: it may carry a credential.
            msg = "An HTTP MCP server's url is an http(s) address with a host"
            raise ValueError(msg)


type McpServerConfig = StdioMcpServer | HttpMcpServer


def read_only_names(servers: Sequence[McpServerConfig] | None) -> frozenset[str]:
    """The names of the servers of ``servers`` whose tools only read."""
    return frozenset(server.name for server in servers or () if server.read_only)
