"""Per agent-run binding of WFEL providers into harness FetchEngine.

[INPUT]
- myrm_agent_harness.toolkits.web_fetch.escalation.context::bind_web_fetch_escalation_context (POS: Escalation context binding)
- app.services.web_fetch.escalation.registry::build_escalation_providers (POS: Provider chain builder)

[OUTPUT]
- open_web_fetch_escalation_context: Async context manager binding WFEL providers for an agent run.

[POS]
Per agent-run binding of web fetch escalation providers into harness FetchEngine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from myrm_agent_harness.toolkits.web_fetch.escalation.context import bind_web_fetch_escalation_context

from app.services.web_fetch.escalation.registry import build_escalation_providers


@asynccontextmanager
async def open_web_fetch_escalation_context(
    *,
    session_id: str,
) -> AsyncIterator[None]:
    """Bind L4 providers for one agent stream run."""
    providers = await build_escalation_providers(session_id)

    with bind_web_fetch_escalation_context(providers=providers):
        yield
