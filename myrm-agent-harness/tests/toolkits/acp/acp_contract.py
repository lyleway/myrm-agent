"""What an ACP agent's ``initialize`` answer must still carry for the runtime to drive it, and the judge of that answer.

``AcpRuntime`` sends images only to an agent that advertises ``promptCapabilities.image``, closes a session only
when ``sessionCapabilities.close`` is there, and continues a stored conversation by ``session/resume`` or else
``session/load``, whichever the agent advertised. Listing, forking and extra workspace roots are held to the same record,
though nothing acts on them yet. An agent that stops advertising one of them changes what a delegated turn can do without
any of our code changing, so each agent's answer is held to what it advertised when the runtime was built on it. The
judge compares subsets: an agent that starts advertising more is no regression.
"""

from __future__ import annotations

from acp.schema import InitializeResponse

from myrm_agent_harness.toolkits.acp.drivers import all_drivers, resolve_acp_args

# The arguments each agent is started with in ACP mode: the known agents' by their contract, the others' as documented.
ACP_ARGS: dict[str, tuple[str, ...]] = {
    "opencode": ("acp",),
    **{
        spec.backend: tuple(resolve_acp_args(spec.backend, []).args)
        for spec in all_drivers()
        if spec.transport == "acp"
    },
}

SESSION_CAPABILITIES = ("list", "delete", "additional_directories", "fork", "resume", "close")

# The session capabilities each agent advertised on 2026-11-26 (opencode 2.3.2, gemini 0.71.1, qwen 0.28.6).
ADVERTISED_SESSION_CAPABILITIES: dict[str, frozenset[str]] = {
    "opencode": frozenset({"list", "delete", "additional_directories", "fork", "resume", "close"}),
    "gemini": frozenset(),
    "qwen": frozenset({"list", "resume"}),
}


def handshake_gaps(agent: str, init: InitializeResponse, *, protocol_version: int) -> list[str]:
    """What ``init`` no longer carries of what the runtime needs from ``agent``; empty when nothing is missing."""
    gaps: list[str] = []
    if init.protocol_version != protocol_version:
        gaps.append(f"protocol version {init.protocol_version}, the client speaks {protocol_version}")
    capabilities = init.agent_capabilities
    if capabilities is None:
        return [*gaps, "no agent capabilities"]
    if capabilities.load_session is not True:
        gaps.append("loadSession")
    if capabilities.prompt_capabilities is None or capabilities.prompt_capabilities.image is not True:
        gaps.append("promptCapabilities.image")
    if capabilities.mcp_capabilities is None or capabilities.mcp_capabilities.http is not True:
        gaps.append("mcpCapabilities.http")
    if not init.auth_methods:
        gaps.append("authMethods (a refused login could not be explained)")
    session = capabilities.session_capabilities
    advertised = {name for name in SESSION_CAPABILITIES if session is not None and getattr(session, name) is not None}
    gaps.extend(f"sessionCapabilities.{name}" for name in sorted(ADVERTISED_SESSION_CAPABILITIES[agent] - advertised))
    return gaps
