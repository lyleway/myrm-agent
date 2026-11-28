# acp/

## Overview
ACP toolkit: Myrm delegating work to external agents (ACP agents, CLI agents, app-server agents). Provides lazy-loaded access to the runtime components. The opposite direction, an IDE driving Myrm over ACP, is [agent/acp](../../agent/acp/_ARCH.md); nothing here depends on it.

Detailed design: [ACP_SYSTEM.md](ACP_SYSTEM.md)

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | ACP toolkit entry point. Provides lazy-loaded access to the runtime components for delegating to external agents | ✅ |
| acp_agent_tools.py | Core | Invoke external ACP-compatible agents (`invoke_acp_agent_tool`, `create_invoke_acp_agent_tool`); the host names the workspace every delegated turn starts in, and the tool hands the model's task to the agent as written. | ✅ |
| client_identity.py | Config | `ClientIdentity` — the name, title and version a host announces to every agent it starts (`RuntimeConfig.client`): one identity for the ACP `initialize` and the Codex app-server handshake; the name is restricted to what an agent repeats verbatim in its user-agent string, the string the host reads the Codex server version from | ✅ |
| delegate_report.py | Internal | What a delegated turn reports: its usage and counts, the title-only notice steps the UI words itself, and the note the model gets when an agent stopped before it finished. | ✅ |
| mcp_servers.py | Config | The MCP servers a host hands a delegated agent: `StdioMcpServer` (a program the agent starts) and `HttpMcpServer` (an endpoint it connects to over streamable HTTP, with the headers it connects with, a credential among them), checked when built (a name of letters and digits joined by single `-` or `_`, because agents build `mcp__<name>__<tool>` names from it and no `__` may hide inside; an `http(s)` URL with a host) and keeping `env`, `headers` and the URL out of `repr` and error messages. `read_only` says the server's tools only read, so a `safe` agent runs them without asking; `read_only_names` lists those of a turn. | ✅ |
| types.py | Config | ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, including `StopReason` (why an agent ended a turn: only the endings the user must hear about have a notice) and the two host notices of a turn that ran without something it was given (`images_not_sent_event`, `mcp_not_attached_event`) | ✅ |

| Submodule | Description |
|-----------|-------------|
| auth/ | Subscription authentication for external CLI backends. See [auth/_ARCH.md](auth/_ARCH.md). |
| core/ | Shared infrastructure — CLI backend detection and the process probe it shares with the login-state checks. |
| drivers/ | The contract of every known external agent: login, transport, launch and permission facts. See [drivers/_ARCH.md](drivers/_ARCH.md). |
| runtime/ | ACP Runtime backends — unified interface for ACP and CLI agents. |
| toolchains/ | Isolated toolchain manager for external CLI agents. See [toolchains/_ARCH.md](toolchains/_ARCH.md). |

## Key Dependencies

- `myrm_agent_harness.core`
- `myrm_agent_harness.utils`
- Optional: `[acp]` → agent-client-protocol (`runtime/acp/`; shared with the inbound server in `agent/acp/`)
