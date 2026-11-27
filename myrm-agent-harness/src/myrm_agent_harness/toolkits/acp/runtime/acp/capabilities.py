"""What an ACP agent said it can do in ``initialize``.

[INPUT]
- acp.schema::InitializeResponse (POS: ACP SDK; optional extra, imported for typing only)

[OUTPUT]
- AgentCapabilities: the capabilities of one agent that the runtime acts on

[POS]
ACP capability negotiation. Every optional ACP method is conditional on what the agent advertised, and an agent is not
asked for what it did not advertise (Gemini answers ``session/resume`` with "method not found"), so the runtime decides
by this record alone. A capability enters it together with the code that acts on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acp.schema import InitializeResponse


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """The capabilities of one agent process; everything it did not advertise is False."""

    # ``promptCapabilities.image``: the prompt may carry image content blocks.
    image_prompts: bool = False
    # ``loadSession``: ``session/load`` continues a stored session and replays its history as updates.
    load_session: bool = False
    # ``sessionCapabilities.resume``: ``session/resume`` continues a stored session without replaying it.
    resume_session: bool = False
    # ``sessionCapabilities.close``: ``session/close`` frees an open session.
    close_session: bool = False
    # ``mcpCapabilities.http``: a session may be given an MCP server to connect to over HTTP (stdio servers need no
    # advertisement: every agent takes them).
    mcp_http: bool = False

    @classmethod
    def of(cls, response: InitializeResponse) -> AgentCapabilities:
        """The capabilities ``response`` advertises."""
        advertised = response.agent_capabilities
        if advertised is None:
            return cls()
        sessions = advertised.session_capabilities
        prompts = advertised.prompt_capabilities
        mcp = advertised.mcp_capabilities
        return cls(
            image_prompts=prompts is not None and bool(prompts.image),
            load_session=bool(advertised.load_session),
            resume_session=sessions is not None and sessions.resume is not None,
            close_session=sessions is not None and sessions.close is not None,
            mcp_http=mcp is not None and bool(mcp.http),
        )
