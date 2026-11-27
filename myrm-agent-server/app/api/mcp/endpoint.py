"""MCP server endpoint for external agent memory access.

[INPUT]
- myrm_agent_harness.toolkits.memory.agent_surface.mcp_server::MemoryMCPServer (POS: MCP server adapter that lets external AI agents access the memory system via standard MCP protocol)
- myrm_agent_harness.toolkits.wiki.mcp_server::register_wiki_mcp_tools (POS: MCP adapter exposing wiki knowledge base tools to external agents)
- app.services.connect::get_connect_service (POS: Token verification)
- app.services.external_agents.memory_grants::get_memory_grants (POS: The memory credentials of delegated external agents) [lazy]
- app.api.mcp.delegation_scope::read_only_request, readable_only, require_readable (POS: The write guard of a delegated agent's credential)
- app.api.mcp.desktop_scope::desktop_session_for_agent, is_desktop_control_enabled_for_agent, set_request_desktop_enabled (POS: Desktop control tools on the endpoint)
- app.api.mcp.wiki_scope::wiki_boundary_enabled_for_agent, wiki_tools_for_agent (POS: Wiki on the endpoint: the profile's flag and its tool bundle)
- app.core.memory.adapters.setup::create_memory_manager, resolve_context_binding (POS: MemoryManager factory)

[OUTPUT]
- setup_mcp_endpoint: async init + mount on FastAPI app
- shutdown_mcp_endpoint: cancel session manager task group

[POS]
Exposes the memory system and wiki knowledge base as a stateless Streamable HTTP
MCP endpoint that external agents (Claude Code, Cursor, etc.) can connect to.
Each Bearer token carries an agent_id: a Connect token (an external client the user connected, full tool set) or a
delegation credential (an external agent Myrm itself delegated to, which only reads: its listing and its calls are
held to the read-only tools). Middleware dynamically binds the
MemoryManager and the wiki tool bundle to the target Agent Profile + SharedContext
via ContextVar, and sets wiki boundary rejection for memory_store when the agent
profile enables wiki. Wiki tools are visible only when the profile enables wiki;
wiki_ingest on this surface accepts URLs and raw text only (local file paths are
refused). Mounted at /mcp on the FastAPI application during startup.

Stateless mode (no Mcp-Session-Id tracking) is used because all four memory
tools are inherently per-request — auth and agent scoping are handled
entirely by the Bearer token middleware, not by MCP session state.

FastAPI's mount() does not propagate lifespan events to sub-apps, so we
manually start the MCP session manager's task group via a background task
and cancel it on shutdown (the SDK requires the task group even in stateless
mode for per-request transport lifecycle).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.mcp.delegation_scope import read_only_request, readable_only, require_readable
from app.api.mcp.desktop_scope import (
    clear_mcp_desktop_sessions,
    desktop_session_for_agent,
    get_request_desktop_enabled,
    is_desktop_control_enabled_for_agent,
    reset_request_desktop_enabled,
    set_request_desktop_enabled,
)
from app.api.mcp.wiki_scope import wiki_boundary_enabled_for_agent, wiki_tools_for_agent

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from mcp.server.mcpserver import Context
    from mcp.types import CallToolResult, InputRequiredResult, Tool
    from myrm_agent_harness.toolkits.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

_session_manager_task: asyncio.Task[None] | None = None
_session_manager_ready = asyncio.Event()


async def _memory_manager_for_agent(agent_id: str) -> MemoryManager:
    from app.core.memory.adapters.setup import (
        create_memory_manager,
        resolve_context_binding,
    )
    from app.services.agent.platform_config import load_platform_retrieval_configs
    from app.services.memory.shared_context.shared_context import (
        resolve_shared_context_ids,
    )

    shared_context_ids = await resolve_shared_context_ids(agent_id=agent_id)
    binding = resolve_context_binding(
        namespaces=None,
        agent_id=agent_id,
        channel_id=None,
        conversation_id=None,
        task_id=None,
        shared_context_ids=shared_context_ids,
    )
    embedding_cfg, _ = await load_platform_retrieval_configs()
    return await create_memory_manager(
        binding,
        embedding_cfg,
        approval_required=False,
    )


class _MCPTokenAuthMiddleware:
    """ASGI middleware validating Bearer tokens for MCP requests.

    A Connect token opens the whole endpoint to the client the user connected. A delegation credential opens the
    memory of the agent it was issued for, read only, to the external agent Myrm delegated a turn to.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        auth_header = request.headers.get("authorization", "")

        if not auth_header.startswith("Bearer "):
            response = JSONResponse(
                {"error": "Missing or invalid Authorization header"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        token = auth_header[7:]
        from app.services.connect import get_connect_service
        from app.services.external_agents.memory_grants import get_memory_grants

        grant = get_memory_grants().resolve(token)
        if grant is not None:
            await self._serve(
                scope,
                receive,
                send,
                label=f"delegation:{grant.external_agent}",
                agent_id=grant.agent_id,
                read_only=True,
            )
            return

        service = get_connect_service()
        resolved = service.resolve_token(token)
        if resolved is None:
            response = JSONResponse(
                {"error": "Invalid or revoked token"},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self._serve(
            scope,
            receive,
            send,
            label=resolved.profile_id,
            agent_id=resolved.agent_id,
            expose_desktop=getattr(resolved, "expose_desktop", False),
            on_ready=lambda: service.mark_ready(resolved.profile_id),
        )

    async def _serve(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        label: str,
        agent_id: str,
        read_only: bool = False,
        expose_desktop: bool = False,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        """Run the MCP app with the memory, wiki and desktop scope of ``agent_id`` bound to this request."""
        from myrm_agent_harness.toolkits.memory.agent_surface.mcp_server import (
            reset_request_memory_manager,
            reset_request_wiki_boundary_enabled,
            set_request_memory_manager,
            set_request_wiki_boundary_enabled,
        )
        from myrm_agent_harness.toolkits.wiki.mcp_server import (
            reset_request_wiki_tools,
            set_request_wiki_tools,
        )

        scope["state"] = scope.get("state", {})
        scope["state"]["mcp_profile_id"] = label
        scope["state"]["mcp_agent_id"] = agent_id

        ctx_token = None
        wiki_token = None
        wiki_tools_token = None
        desktop_token = None
        desktop_enabled_token = None
        try:
            manager = await _memory_manager_for_agent(agent_id)
            ctx_token = set_request_memory_manager(manager)
            wiki_enabled = await wiki_boundary_enabled_for_agent(agent_id)
            wiki_token = set_request_wiki_boundary_enabled(wiki_enabled)
            wiki_tools_token = set_request_wiki_tools(
                await wiki_tools_for_agent(agent_id, wiki_enabled=wiki_enabled, read_only=read_only)
            )

            from myrm_agent_harness.toolkits.computer_use.mcp_server import (
                reset_request_desktop_session,
                set_request_desktop_session,
            )

            desktop_eligible = await is_desktop_control_enabled_for_agent(agent_id)
            allow_desktop = desktop_eligible and expose_desktop
            desktop_enabled_token = set_request_desktop_enabled(allow_desktop)
            if allow_desktop:
                desktop_session = desktop_session_for_agent(agent_id)
                desktop_token = set_request_desktop_session(desktop_session)

            if on_ready is not None:
                on_ready()
            with read_only_request(read_only):
                await self.app(scope, receive, send)
        except Exception:
            logger.exception(
                "MCP scope resolution failed for profile=%s agent=%s",
                label,
                agent_id,
            )
            response = JSONResponse(
                {"error": "Memory scope unavailable"},
                status_code=503,
            )
            await response(scope, receive, send)
        finally:
            if desktop_token is not None:
                reset_request_desktop_session(desktop_token)
            if desktop_enabled_token is not None:
                reset_request_desktop_enabled(desktop_enabled_token)
            if wiki_tools_token is not None:
                reset_request_wiki_tools(wiki_tools_token)
            if wiki_token is not None:
                reset_request_wiki_boundary_enabled(wiki_token)
            if ctx_token is not None:
                reset_request_memory_manager(ctx_token)


async def setup_mcp_endpoint(app: FastAPI) -> None:
    """Create and mount the MCP memory endpoint at /mcp.

    Should be called during application startup (Phase 2 or later). No MemoryManager is
    built here: ``_MCPTokenAuthMiddleware`` binds the manager of the token's agent on every
    request, so startup never opens the vector store or reaches the embedding backend.
    Gracefully skips if the MCP server cannot be built or started.
    """
    global _session_manager_task

    try:
        from myrm_agent_harness.toolkits.memory.agent_surface.mcp_server import MemoryMCPServer

        mcp_server = MemoryMCPServer(approval_required=False, stateless_http=True)

        from myrm_agent_harness.toolkits.computer_use.mcp_server import (
            get_request_desktop_session,
            register_desktop_mcp_tools,
        )

        register_desktop_mcp_tools(mcp_server.mcp, get_request_desktop_session)

        from myrm_agent_harness.toolkits.wiki.mcp_server import (
            get_request_wiki_tools,
            register_wiki_mcp_tools,
        )

        register_wiki_mcp_tools(mcp_server.mcp, get_request_wiki_tools)

        orig_list_tools = mcp_server.mcp.list_tools
        orig_call_tool = mcp_server.mcp.call_tool

        async def _filtered_list_tools() -> list[Tool]:
            tools = await orig_list_tools()
            if not get_request_desktop_enabled():
                tools = [t for t in tools if not getattr(t, "name", "").startswith("desktop_")]
            if get_request_wiki_tools() is None:
                tools = [t for t in tools if not getattr(t, "name", "").startswith("wiki_")]
            return readable_only(tools)

        async def _guarded_call_tool(
            name: str,
            arguments: dict[str, object],
            context: Context[object, object] | None = None,
        ) -> CallToolResult | InputRequiredResult:
            # A client can call a tool it was never listed, so hiding it from the listing is not the guard.
            require_readable(name)
            return await orig_call_tool(name, arguments, context)

        from mcp.server.transport_security import TransportSecuritySettings

        mcp_server.mcp.list_tools = _filtered_list_tools  # type: ignore[method-assign]
        mcp_server.mcp.call_tool = _guarded_call_tool  # type: ignore[method-assign]
        mcp_asgi_app = mcp_server.get_streamable_http_app(
            streamable_http_path="/",
            transport_security=TransportSecuritySettings(
                # Loopback names with any port: the SDK matches ``name:*`` only against a Host that carries a port.
                allowed_hosts=[
                    "localhost:*",
                    "127.0.0.1:*",
                ]
            ),
        )

        # FastAPI mount() does not propagate lifespan to sub-apps, so the
        # Starlette sub-app's lifespan (which calls session_manager.run())
        # never fires. We start the session manager manually via a background
        # task that keeps the anyio TaskGroup alive for the server lifetime.
        sm = mcp_server.mcp.session_manager
        if sm is not None:

            async def _run_session_manager() -> None:
                async with sm.run():
                    _session_manager_ready.set()
                    # Block until cancelled on shutdown
                    await asyncio.Event().wait()

            _session_manager_task = asyncio.create_task(_run_session_manager())
            await asyncio.wait_for(_session_manager_ready.wait(), timeout=5.0)

        from app.api.mcp.origin_guard import _MCPOriginGuardMiddleware, resolve_origin_guard
        from app.config.settings import settings

        authed_app = _MCPTokenAuthMiddleware(mcp_asgi_app)
        origin_guard = resolve_origin_guard(host=getattr(settings, "host", "127.0.0.1"))
        guarded_app = _MCPOriginGuardMiddleware(authed_app, guard=origin_guard)
        app.mount("/mcp", guarded_app)
        logger.info("MCP memory endpoint mounted at /mcp (DNS-rebinding protected)")

    except Exception as e:
        logger.warning("MCP endpoint not mounted: %s", e)


async def shutdown_mcp_endpoint() -> None:
    """Cancel the MCP session manager background task on shutdown."""
    global _session_manager_task, _session_manager_ready
    clear_mcp_desktop_sessions()
    if _session_manager_task is not None:
        _session_manager_task.cancel()
        try:
            await _session_manager_task
        except asyncio.CancelledError:
            pass
        _session_manager_task = None
        # Rebuild the ready event instead of reusing it: an asyncio.Event used
        # once stays bound to that event loop (raising "bound to a different
        # event loop" under the next setup), and a stale set flag would let
        # the next setup proceed before its fresh session manager enters
        # run(), surfacing as "Task group is not initialized" on the first
        # MCP request.
        _session_manager_ready = asyncio.Event()
        logger.info("MCP session manager stopped")
