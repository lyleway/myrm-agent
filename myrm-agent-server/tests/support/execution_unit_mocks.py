"""Typed access to the mock an execution unit is built around."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

from app.services.agent.execution_cache.types import BuiltExecutionUnit


def agent_close(unit: BuiltExecutionUnit) -> AsyncMock:
    """The ``close`` of a unit whose agent is a mock with an ``AsyncMock`` close, for ``assert_awaited_*`` checks."""
    return cast(AsyncMock, unit.skill_agent.close)
