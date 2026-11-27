# acp/

## Overview
ACP toolkit entry point. Provides lazy-loaded access to server and runtime components

Detailed design: [ACP_SYSTEM.md](ACP_SYSTEM.md)

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | ACP toolkit entry point. Provides lazy-loaded access to server and runtime components | ✅ |
| __main__.py | Internal | CLI entry point for the ACP server. | ✅ |
| acp_agent_tools.py | Core | Invoke external ACP-compatible agents (`invoke_acp_agent_tool`, `create_invoke_acp_agent_tool`); the host names the workspace every delegated turn starts in, and the tool hands the model's task to the agent as written. | ✅ |
| delegate_report.py | Internal | What a delegated turn reports: its usage and counts, the title-only notice steps the UI words itself, and the note the model gets when an agent stopped before it finished. | ✅ |
| mcp_servers.py | Config | The MCP servers a host hands a delegated agent: `StdioMcpServer` (a program the agent starts) and `HttpMcpServer` (an endpoint it connects to over streamable HTTP, with the headers it connects with, a credential among them), checked when built (a name of letters and digits joined by single `-` or `_`, because agents build `mcp__<name>__<tool>` names from it and no `__` may hide inside; an `http(s)` URL with a host) and keeping `env`, `headers` and the URL out of `repr` and error messages. `read_only` says the server's tools only read, so a `safe` agent runs them without asking; `read_only_names` lists those of a turn. | ✅ |
| types.py | Config | ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, including `StopReason` (why an agent ended a turn: only the endings the user must hear about have a notice) | ✅ |

| Submodule | Description |
|-----------|-------------|
| auth/ | Subscription authentication for external CLI backends. See [auth/_ARCH.md](auth/_ARCH.md). |
| core/ | Shared runtime infrastructure — event bus, backend detector. |
| runtime/ | ACP Runtime backends — unified interface for ACP and CLI agents. |
| server/ | ACP Server — bridges IDE clients to the agent system via ACP protocol. |
| toolchains/ | Isolated toolchain manager for external CLI agents. See [toolchains/_ARCH.md](toolchains/_ARCH.md). |

## Key Dependencies

- `myrm_agent_harness.core`
- `myrm_agent_harness.utils`
- Optional: `[acp]` → agent-client-protocol (server/, bridge/, event_translator/)
