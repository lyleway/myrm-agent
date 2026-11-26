"""A ``GeneralAgent`` stand-in for a turn forced onto one external agent, with the real security assembly.

[INPUT]
- app.ai_agents.general_agent.tool_setup.base::ToolSetupBaseMixin (POS: the effective security config of a turn, bound unchanged)
- app.core.channel_bridge.config_loader::load_user_configs (POS: where the saved securityConfig is read, patched by ``saved_security``)

[OUTPUT]
- forced_delegate_agent: the stand-in; carries what the forced-delegate branch reads and nothing of the normal path
- saved_security: patches the store the saved securityConfig is read from, for the turns of one test

[POS]
The gate of a forced turn is judged on the effective config of the turn (channel preset, saved config, agent
overrides, request overlays), so a stand-in that carries only a raw dict cannot show what it does. This one binds the
production ``_security_config_for`` and reads the saved config through the production reload, so the inputs of a case
are the ones a request brings: the store, the agent's own overrides and the overlay.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai_agents.general_agent.tool_setup.base import ToolSetupBaseMixin
from app.core.security.session_overlay import SecurityOverlaySpec

DELEGATED = {"type": "tasks_steps", "data": "delegated"}


def forced_delegate_agent(
    *,
    backends: list[str] | None,
    locale: str | None = "en",
    channel: str | None = "web_chat",
    agent_security: dict[str, object] | None = None,
    overlay: SecurityOverlaySpec | None = None,
    breaks_with: Exception | None = None,
) -> SimpleNamespace:
    """The stand-in: ``backends`` is the pool the agent ends up with (None: no pool could be built).

    ``breaks_with`` makes the delegated turn raise after its first event. It has no ``jit_subagents`` or any other
    normal-path attribute, so a fall-through to the normal agent raises.
    """
    agent = SimpleNamespace(
        agent_id=None,
        channel_name=channel,
        agent_security_raw=agent_security,
        security_config_raw=None,
        security_overlay=overlay,
        declared_capabilities=(),
        declared_allowed_roots=(),
        locale=locale,
        _runtime_pool=None,
        delegated=[],
        queries=[],
        pool_requests=0,
    )

    async def ensure_pool() -> None:
        agent.pool_requests += 1
        agent._runtime_pool = SimpleNamespace(available_backends=backends) if backends is not None else None

    async def delegate(
        name: str, query: object, *, cancel_token: object = None, chat_id: str | None = None
    ) -> AsyncGenerator[dict[str, object]]:
        agent.delegated.append(name)
        agent.queries.append(query)
        yield DELEGATED
        if breaks_with is not None:
            raise breaks_with

    agent._ensure_runtime_pool = ensure_pool
    agent._direct_delegate_stream = delegate
    agent._security_config_for = MethodType(ToolSetupBaseMixin._security_config_for, agent)
    return agent


@contextmanager
def saved_security(config: dict[str, object] | None) -> Iterator[AsyncMock]:
    """Make ``config`` the securityConfig the store holds (None: the user never saved one) for the enclosed turns."""
    configs = SimpleNamespace(security_config_dict=config)
    with patch("app.core.channel_bridge.config_loader.load_user_configs", new=AsyncMock(return_value=configs)) as load:
        yield load
