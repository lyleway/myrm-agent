"""What the host says to Codex's app server: the handshake, the policy of a turn, and the shape of its requests.

[INPUT]
- toolkits.acp.client_identity::ClientIdentity (POS: How a host introduces itself to the agents it delegates to)
- toolkits.acp.runtime.codex.wire::Json, as_bool, as_list, as_obj, as_str (POS: Typed reads of the decoded JSON that Codex's app server sends)
- toolkits.acp.mcp_servers::HttpMcpServer, McpServerConfig, StdioMcpServer (POS: The MCP servers a host hands a delegated agent)
- toolkits.acp.types::PermissionMode (POS: ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, serving as the foundation for the entire ACP module.)

[OUTPUT]
- OPT_OUT_NOTIFICATIONS: the notification methods the host never reads, which the server is asked not to send
- MINIMUM_SERVER_VERSION, server_version: the oldest server the host speaks to, and the version a server says it is
- initialize_params, login_params, config_read_params: the handshake, the API key login and the read of the user's config
- WorkspaceWriteSettings: the user's own settings of the workspace-write sandbox
- TurnPolicy, turn_policy: the approval policy and sandbox a permission mode stands for
- thread_params, resume_params, turn_params: the requests that open a thread and run a turn on it

[POS]
The host side of the app-server protocol, kept apart from the runtime so the one place that decides what a permission
mode means to Codex is small and testable. Every field here is checked against the schema the installed Codex generates
(``codex app-server generate-json-schema``) by the real-binary contract tests.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from myrm_agent_harness.toolkits.acp.client_identity import ClientIdentity
from myrm_agent_harness.toolkits.acp.mcp_servers import HttpMcpServer, McpServerConfig, StdioMcpServer
from myrm_agent_harness.toolkits.acp.runtime.codex.wire import Json, as_bool, as_list, as_obj, as_str
from myrm_agent_harness.toolkits.acp.types import PermissionMode

# The oldest app server the host speaks to: the real-binary contract tests run against exactly this one in CI. An older
# one asks to approve an MCP tool without naming the tool, so a person would be asked to allow a call without being
# told which one.
MINIMUM_SERVER_VERSION: tuple[int, int, int] = (0, 167, 0)

_SERVER_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# What a turn turns into events is only the stream of items, text and usage; the rest (command output chunks, rate
# limits, thread bookkeeping) is volume the host would parse and drop, a build log of megabytes included.
OPT_OUT_NOTIFICATIONS: tuple[str, ...] = (
    "account/rateLimits/updated",
    "account/updated",
    "app/list/updated",
    "command/exec/outputDelta",
    "fs/changed",
    "hook/completed",
    "hook/started",
    "item/autoApprovalReview/completed",
    "item/autoApprovalReview/started",
    "item/commandExecution/outputDelta",
    "item/commandExecution/terminalInteraction",
    "item/fileChange/outputDelta",
    "item/fileChange/patchUpdated",
    "item/mcpToolCall/progress",
    "item/plan/delta",
    "process/outputDelta",
    "remoteControl/status/changed",
    "skills/changed",
    "thread/queue/changed",
    "thread/readState/changed",
    "thread/settings/updated",
    "thread/started",
    "thread/status/changed",
    "turn/diff/updated",
    "turn/plan/updated",
)


def initialize_params(client: ClientIdentity) -> dict[str, object]:
    """The handshake: who the client is, and the notifications it does not want."""
    return {
        "clientInfo": {"name": client.name, "title": client.title, "version": client.version},
        "capabilities": {"optOutNotificationMethods": list(OPT_OUT_NOTIFICATIONS)},
    }


def server_version(greeting: Json, client: ClientIdentity) -> tuple[int, int, int] | None:
    """The version of the server that answered ``initialize``, or None when it does not say.

    The server reports ``userAgent`` as ``<client name>/<server version> (...)``, with the name the client announced.
    """
    user_agent = as_str(greeting.get("userAgent"))
    head = f"{client.name}/"
    found = _SERVER_VERSION.match(user_agent, len(head)) if user_agent.startswith(head) else None
    return (int(found[1]), int(found[2]), int(found[3])) if found else None


def login_params(api_key: str) -> dict[str, object]:
    """The login with an API key: it is held by the server as long as the process lives (see ``resolve_app_server_args``)."""
    return {"type": "apiKey", "apiKey": api_key}


def config_read_params(workspace: str) -> dict[str, object]:
    """``config/read``: the settings in force for ``workspace`` (the user's ``config.toml`` and the project's)."""
    return {"includeLayers": False, "cwd": workspace}


@dataclass(frozen=True, slots=True)
class WorkspaceWriteSettings:
    """What the user's own config says about the workspace-write sandbox: the extra directories it may write, the network."""

    writable_roots: tuple[str, ...] = ()
    network_access: bool = False
    exclude_tmpdir_env_var: bool = False
    exclude_slash_tmp: bool = False

    @classmethod
    def of(cls, config: Json) -> WorkspaceWriteSettings:
        """The settings of ``config/read``'s ``sandbox_workspace_write`` table (defaults where the user set nothing)."""
        table = as_obj(config.get("sandbox_workspace_write"))
        return cls(
            writable_roots=tuple(root for root in as_list(table.get("writable_roots")) if isinstance(root, str)),
            network_access=as_bool(table.get("network_access")),
            exclude_tmpdir_env_var=as_bool(table.get("exclude_tmpdir_env_var")),
            exclude_slash_tmp=as_bool(table.get("exclude_slash_tmp")),
        )

    def policy(self) -> dict[str, object]:
        return {
            "type": "workspaceWrite",
            "writableRoots": list(self.writable_roots),
            "networkAccess": self.network_access,
            "excludeTmpdirEnvVar": self.exclude_tmpdir_env_var,
            "excludeSlashTmp": self.exclude_slash_tmp,
        }


@dataclass(frozen=True, slots=True)
class TurnPolicy:
    """The approval policy and the sandbox one turn runs under.

    ``sandbox`` is the mode a thread is opened with, ``sandbox_policy`` the policy a turn is given: a turn states its
    policy itself, so a thread that served an asking turn can serve an unattended one next.
    """

    approval: str
    sandbox: str
    sandbox_policy: dict[str, object]


# ``safe`` and somebody to ask (Codex's own Read Only preset): whatever only reads runs inside a read-only sandbox
# without asking, and whatever needs more (a write, the network, a patch) is asked, with the reason the agent gives.
# ``untrusted`` would ask about every command, ``pwd`` and ``cat`` included.
_ASKING = TurnPolicy("on-request", "read-only", {"type": "readOnly", "networkAccess": False})
# ``safe`` and nobody to ask: Codex never asks, so it may only read.
_READ_ONLY = TurnPolicy("never", "read-only", {"type": "readOnly", "networkAccess": False})


def turn_policy(mode: PermissionMode, *, can_ask: bool, workspace_write: WorkspaceWriteSettings) -> TurnPolicy:
    """The policy of a turn: ``allow_all`` acts inside the user's workspace-write sandbox, ``safe`` asks or only reads."""
    if mode == "allow_all":
        return TurnPolicy("never", "workspace-write", workspace_write.policy())
    return _ASKING if can_ask else _READ_ONLY


def _mcp_server_entry(server: McpServerConfig) -> dict[str, object]:
    """One ``mcp_servers`` entry of a thread's config: a program to start, or a ``url`` to connect to with headers."""
    # Codex starts a server in the background unless it is ``required``: a turn that began first offered the model none
    # of its tools, and one that failed to start was never reported. A required one is waited for when the thread
    # opens, and the thread does not open (with the server's own error) when it cannot start.
    entry: dict[str, object]
    match server:
        case StdioMcpServer():
            entry = {"command": server.command, "args": list(server.args), "required": True}
            if server.env:
                entry["env"] = dict(server.env)
        case HttpMcpServer():
            entry = {"url": server.url, "required": True}
            if server.headers:
                entry["http_headers"] = dict(server.headers)
    if server.read_only:
        # Codex asks before every MCP tool call under ``on-request``; a server whose tools only read is not asked about.
        entry["default_tools_approval_mode"] = "approve"
    return entry


def _mcp_servers_config(servers: Sequence[McpServerConfig]) -> dict[str, object]:
    return {"mcp_servers": {server.name: _mcp_server_entry(server) for server in servers}}


def thread_params(
    workspace: str, policy: TurnPolicy, *, mcp_servers: Sequence[McpServerConfig], ephemeral: bool
) -> dict[str, object]:
    """``thread/start``: a conversation rooted in ``workspace``; ``ephemeral`` keeps it off the disk."""
    params: dict[str, object] = {"cwd": workspace, "approvalPolicy": policy.approval, "sandbox": policy.sandbox}
    if mcp_servers:
        params["config"] = _mcp_servers_config(mcp_servers)
    if ephemeral:
        params["ephemeral"] = True
    return params


def resume_params(
    thread_id: str, workspace: str, policy: TurnPolicy, *, mcp_servers: Sequence[McpServerConfig]
) -> dict[str, object]:
    """``thread/resume``: the conversation ``thread_id`` again, without its history."""
    params = thread_params(workspace, policy, mcp_servers=mcp_servers, ephemeral=False)
    params["threadId"] = thread_id
    params["excludeTurns"] = True
    return params


def turn_params(thread_id: str, prompt: str, images: Sequence[str], policy: TurnPolicy) -> dict[str, object]:
    """``turn/start``: the prompt (the images by path, never through a command line) under ``policy``."""
    user_input: list[Mapping[str, object]] = [{"type": "text", "text": prompt, "text_elements": []}]
    user_input.extend({"type": "localImage", "path": path} for path in images)
    return {
        "threadId": thread_id,
        "input": user_input,
        "approvalPolicy": policy.approval,
        "sandboxPolicy": policy.sandbox_policy,
    }
