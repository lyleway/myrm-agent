"""Remote fetch escalation hooks for FetchEngine (L4 after the local HTTP read).

Framework layer defines Protocol + DTOs only; httpx vendor implementations live in server.
"""

from .protocols import EscalationFetchResult, FetchEscalationProvider

__all__ = [
    "EscalationFetchResult",
    "FetchEscalationProvider",
]
