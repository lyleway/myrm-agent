# agent/acp/

## Overview
Myrm as an ACP agent: an IDE (Cursor, Zed, VS Code) connects over the Agent Client Protocol and drives a Myrm agent; `python -m myrm_agent_harness.agent.acp` starts the standalone server. The protocol server, its session bridge and the agent factories live here. `toolkits/acp/` is the opposite direction (Myrm driving external agents); the two share nothing but the optional `acp` SDK, and `toolkits` never imports this package.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Lazy exports of the inbound surface (server, bridge, MCP converter, both factories); importing a protocol module does not load the agent stack the factories need | ✅ |
| server.py | Core | `MyrmAcpServer` (the `acp.Agent` a client connects to; the MCP servers a client hands over are bound into the session's agent) and `run_server` (over stdio, or over the streams it is given) | ✅ |
| bridge.py | Core | ACP session lifecycle: session-to-agent mapping, prompt forwarding, translated events streamed back, an agent failure turned into a visible message; `AgentFactory` is the protocol a host implements to build the agent of a session | ✅ |
| event_translator.py | Core | AgentEvent → ACP SessionNotification translation (pure: no state) | ✅ |
| mcp_converter.py | Adapter | The MCP servers a client hands over (ACP schema) → Myrm `MCPConfig` | ✅ |
| default_factory.py | Core | DefaultAgentFactory — creates BaseAgent per ACP session. LLM: injected model, else `LLMConfig`, else `MYRM_*` env via `LLMManager.get_llm_from_config`; with none configured `create_agent` raises (no stand-in model) | ✅ |
| skill_factory.py | Core | SkillAgentFactory — creates full-featured SkillAgent with dynamic MCP per ACP session | ✅ |
| __main__.py | CLI | Module entry for standalone ACP server (stdio / UDS socket). Its one log sink is stderr (stdout carries the protocol) through `RedactingFormatter`, which masks the secret-shaped strings of each line as it does in the server's logs | ✅ |

## Division vs toolkits/acp/

| Package | Responsibility |
|---------|----------------|
| `agent/acp/` | Inbound: an IDE drives Myrm — protocol server, session bridge, agent factories, CLI entry |
| `toolkits/acp/` | Outbound: Myrm drives external agents — runtime backends, drivers, auth, permission, toolchains |

## Module Dependencies

- `acp` (POS: Agent Client Protocol SDK; optional `[acp]` extra, imported by `server.py`, `bridge.py` and `event_translator.py`)
- `toolkits.mcp.config::MCPConfig` (POS: MCP server configuration; the target of `mcp_converter.py`)
- `utils.runtime.cancellation::CancellationToken` (POS: Cancels the running turn of a session) [lazy]
- `agent.base_agent::BaseAgent` (POS: Lightweight agent with streaming and artifacts)
- `core.config.llm::LLMConfig` (POS: LLM configuration; `from_env()` reads `MYRM_MODEL_NAME` / `MYRM_API_KEY`)
- `toolkits.llms::LLMManager` (POS: Cached `LLMConfig` to chat model construction) [lazy]
- `core.security.redact::RedactingFormatter` (POS: Log formatter that masks secret-shaped strings in every line)
