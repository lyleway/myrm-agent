"""What a host may hand a delegated agent as an MCP server, and what a logged config gives away."""

from __future__ import annotations

import pytest

from myrm_agent_harness.toolkits.acp.mcp_servers import (
    HttpMcpServer,
    McpServerConfig,
    StdioMcpServer,
    read_only_names,
)


@pytest.mark.parametrize("name", ["memory", "my-server_1", "A", "x" * 64, "a-b-c", "a_b"])
def test_a_name_of_words_joined_by_a_dash_or_an_underscore_is_accepted(name: str) -> None:
    assert StdioMcpServer(name=name, command="mcp").name == name
    assert HttpMcpServer(name=name, url="http://127.0.0.1:1/mcp").name == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "my server",
        "a.b",
        "a/b",
        "a*",
        "memory\n",
        "x" * 65,
        "mem\u00f6ry",
        "a__b",
        "_a",
        "a_",
        "-a",
        "a-",
        "a--b",
        "a_-b",
    ],
)
def test_any_other_name_is_refused_because_agents_build_tool_names_and_flags_from_it(name: str) -> None:
    with pytest.raises(ValueError, match="MCP server name"):
        StdioMcpServer(name=name, command="mcp")
    with pytest.raises(ValueError, match="MCP server name"):
        HttpMcpServer(name=name, url="http://127.0.0.1:1/mcp")


def test_a_stdio_server_needs_a_command() -> None:
    with pytest.raises(ValueError, match="needs a command"):
        StdioMcpServer(name="local", command="")


@pytest.mark.parametrize("url", ["http://127.0.0.1:9/mcp", "https://example.test/mcp", "http://localhost/mcp"])
def test_an_http_or_https_address_is_a_url(url: str) -> None:
    assert HttpMcpServer(name="memory", url=url).url == url


@pytest.mark.parametrize(
    "url", ["", "127.0.0.1:9/mcp", "ftp://example.test/mcp", "file:///etc/passwd", "http://", "/mcp"]
)
def test_anything_else_is_refused_without_echoing_it(url: str) -> None:
    with pytest.raises(ValueError, match="http\\(s\\) address") as error:
        HttpMcpServer(name="memory", url=url)

    assert url not in str(error.value) or url == ""


def test_the_headers_and_the_environment_do_not_appear_in_a_logged_config() -> None:
    http = HttpMcpServer(name="memory", url="http://127.0.0.1:9/mcp", headers={"Authorization": "Bearer s3cret"})
    stdio = StdioMcpServer(name="local", command="mcp", env={"API_KEY": "s3cret"})

    assert "s3cret" not in repr(http)
    assert "s3cret" not in repr(stdio)


def test_a_server_is_not_read_only_unless_the_host_says_so() -> None:
    assert not HttpMcpServer(name="memory", url="http://127.0.0.1:9/mcp").read_only
    assert not StdioMcpServer(name="local", command="mcp").read_only


def test_the_read_only_names_are_those_of_the_servers_declared_so() -> None:
    servers: list[McpServerConfig] = [
        HttpMcpServer(name="memory", url="http://127.0.0.1:9/mcp", read_only=True),
        StdioMcpServer(name="local", command="mcp"),
        StdioMcpServer(name="notes", command="mcp", read_only=True),
    ]

    assert read_only_names(servers) == {"memory", "notes"}
    assert read_only_names([]) == frozenset()
    assert read_only_names(None) == frozenset()
