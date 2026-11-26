"""Request-scoped escalation bindings (server sets per agent run; harness reads).

[POS]
ContextVar bindings for optional L4 fetch escalation providers.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.web_fetch.escalation.protocols import FetchEscalationProvider

_providers_ctx: ContextVar[list[FetchEscalationProvider] | None] = ContextVar(
    "web_fetch_escalation_providers",
    default=None,
)


def get_bound_escalation_providers() -> list[FetchEscalationProvider] | None:
    return _providers_ctx.get()


@contextmanager
def bind_web_fetch_escalation_context(
    *,
    providers: list[FetchEscalationProvider] | None,
) -> Iterator[None]:
    """Bind escalation providers for the current async task."""
    provider_token: Token[list[FetchEscalationProvider] | None] = _providers_ctx.set(providers)
    try:
        yield
    finally:
        try:
            _providers_ctx.reset(provider_token)
        except ValueError:
            # Token was created in a different async context (LangGraph cancel / TestClient).
            logger.warning(
                "Context variable reset skipped: token created in different context (%s)",
                _providers_ctx.name,
            )
