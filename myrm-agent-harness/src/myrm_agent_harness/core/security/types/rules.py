"""Permission model of the security subsystem: capability grants, the rule vocabulary and the default ruleset.

[INPUT]
- (none — pure types, zero dependencies)

[OUTPUT]
- Capability / CapabilitySet / DEFAULT_CAPABILITIES: Layer 1 capability grants
- PermissionAction: allow / ask / deny
- CapabilitySurface / CAPABILITY_SURFACE_PERMISSIONS: the six coarse capability surfaces and the permission types each governs
- PermissionRule / PermissionRuleset / DEFAULT_RULESET: Layer 3 rules

[POS]
The vocabulary the engine and every other security type speak: what a session may reach (capabilities) and what a
call costs (rules). Imports nothing from the security subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Capability:
    """A single capability grant or exclusion: (permission_type, resource_pattern).

    Capabilities are deny-by-default: only explicitly granted capabilities
    are allowed. Once a CapabilitySet is created, it cannot be expanded
    (anti-privilege-escalation via frozenset).

    A ``permission`` prefixed with ``!`` acts as a **negative capability**
    (exclusion). Negative capabilities take precedence over positive ones,
    enabling patterns like "allow everything except browser_*".
    """

    permission: str
    pattern: str


CapabilitySet = frozenset[Capability]

DEFAULT_CAPABILITIES: CapabilitySet = frozenset({Capability("*", "*")})


class PermissionAction(StrEnum):
    """How a tool call should be handled after permission evaluation."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class CapabilitySurface(StrEnum):
    """Six first-class capability surfaces for coarse-to-fine permission matrix control."""

    KNOWLEDGE_READ = "knowledge_read"
    KNOWLEDGE_WRITE = "knowledge_write"
    WEB_EGRESS = "web_egress"
    CANDIDATE_CREATE = "candidate_create"
    REMOTE_TOOLS = "remote_tools"
    LOCAL_FILESYSTEM = "local_filesystem"


# Mapping from capability surface to underlying permission types it governs.
CAPABILITY_SURFACE_PERMISSIONS: dict[CapabilitySurface, tuple[str, ...]] = {
    CapabilitySurface.KNOWLEDGE_READ: ("knowledge_read", "wiki_query_tool"),
    CapabilitySurface.KNOWLEDGE_WRITE: ("knowledge_write", "wiki_apply_tool", "wiki_ingest_tool"),
    CapabilitySurface.WEB_EGRESS: ("net_fetch", "web_fetch", "web_search_tool", "browser_navigate"),
    CapabilitySurface.CANDIDATE_CREATE: ("candidate_create", "todo_write"),
    CapabilitySurface.REMOTE_TOOLS: ("mcp_invoke", "invoke_external_agent"),
    CapabilitySurface.LOCAL_FILESYSTEM: ("file_read", "file_write", "shell_exec", "code_interpreter"),
}


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """A single rule mapping (permission_type, pattern) to an action.

    ``permission`` matches tool categories (e.g. ``"shell_exec"``, ``"file_write"``, ``"*"``).
    ``pattern`` matches the target resource (e.g. ``"*.py"``, ``"api.openai.com"``, ``"*"``).
    Both use fnmatch-style wildcards.
    """

    permission: str
    pattern: str
    action: PermissionAction


PermissionRuleset = tuple[PermissionRule, ...]


DEFAULT_RULESET: PermissionRuleset = (
    PermissionRule("*", "*", PermissionAction.ALLOW),
    PermissionRule("knowledge_read", "*", PermissionAction.ALLOW),
    PermissionRule("knowledge_write", "*", PermissionAction.ASK),
    PermissionRule("shell_exec", "*", PermissionAction.ASK),
    PermissionRule("code_interpreter", "*", PermissionAction.ASK),
    # Sensitive files: credentials, keys, databases
    PermissionRule("file_read", "*.env", PermissionAction.ASK),
    PermissionRule("file_read", "*.env.*", PermissionAction.ASK),
    PermissionRule("file_read", "*.pem", PermissionAction.ASK),
    PermissionRule("file_read", "*.key", PermissionAction.ASK),
    PermissionRule("file_read", "*.db", PermissionAction.ASK),
    PermissionRule("file_read", "*.sqlite", PermissionAction.ASK),
    PermissionRule("file_read", "*.sqlite3", PermissionAction.ASK),
    PermissionRule("file_read", "*credentials.json", PermissionAction.ASK),
    PermissionRule("file_read", "*secrets.json", PermissionAction.ASK),
    PermissionRule("file_read", "*.git/config", PermissionAction.ASK),
    PermissionRule("file_write", "*.env", PermissionAction.ASK),
    PermissionRule("file_write", "*.env.*", PermissionAction.ASK),
    PermissionRule("file_write", "*.pem", PermissionAction.ASK),
    PermissionRule("file_write", "*.key", PermissionAction.ASK),
    PermissionRule("file_write", "*.db", PermissionAction.ASK),
    PermissionRule("file_write", "*.sqlite", PermissionAction.ASK),
    PermissionRule("file_write", "*.sqlite3", PermissionAction.ASK),
    PermissionRule("file_write", "*credentials.json", PermissionAction.ASK),
    PermissionRule("file_write", "*secrets.json", PermissionAction.ASK),
    PermissionRule("file_write", "*.git/config", PermissionAction.ASK),
    PermissionRule("mcp_invoke", "*", PermissionAction.ASK),
    PermissionRule("browser_evaluate", "*", PermissionAction.DENY),
    PermissionRule("browser_upload", "*", PermissionAction.ASK),
    PermissionRule("browser_download", "*", PermissionAction.ASK),
    PermissionRule("browser_fill", "*", PermissionAction.ASK),
    PermissionRule("browser_session", "*", PermissionAction.ASK),
    PermissionRule("browser_human_handover", "*", PermissionAction.ASK),
    PermissionRule("browser_navigate", "127.0.0.1*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "localhost*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "0.0.0.0*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "10.*", PermissionAction.DENY),
    # RFC 1918: 172.16.0.0/12 = 172.16.* ~ 172.31.*
    PermissionRule("browser_navigate", "172.16.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.17.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.18.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.19.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.20.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.21.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.22.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.23.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.24.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.25.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.26.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.27.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.28.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.29.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.30.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "172.31.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "192.168.*", PermissionAction.DENY),
    PermissionRule("browser_navigate", "169.254.*", PermissionAction.DENY),
    PermissionRule("desktop_control", "*", PermissionAction.ASK),
    PermissionRule("skill_manage", "*", PermissionAction.ASK),
    PermissionRule("cron_manage", "*", PermissionAction.ASK),
    PermissionRule("spawn_subagent", "*", PermissionAction.ALLOW),
    PermissionRule("invoke_external_agent", "*", PermissionAction.ALLOW),
)
