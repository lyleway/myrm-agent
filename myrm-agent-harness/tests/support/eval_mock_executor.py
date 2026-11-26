"""A scripted ``AgentExecutor`` for the ``EvalRunner`` tests.

[INPUT]
- eval.protocols::AgentResponse (POS: what an executor answers for one message)
- toolkits.code_execution.executors.base::CodeExecutor (POS: the sandbox executor a session may expose)

[OUTPUT]
- MockExecutor: answers each message from a table or a default, raises for chosen messages, hands out numbered
  sessions and records every call as ``(message, session_id)``

[POS]
The runner tests (single-turn, multi-turn, result) drive ``EvalRunner`` with this one double, so the call log, the
session ids and the injected failures behave the same in every file.
"""

from __future__ import annotations

from myrm_agent_harness.eval.protocols import AgentResponse
from myrm_agent_harness.toolkits.code_execution.executors.base import CodeExecutor


class MockExecutor:
    """Mock AgentExecutor for testing."""

    def __init__(
        self,
        responses: dict[str, AgentResponse] | None = None,
        *,
        default_response: AgentResponse | None = None,
        fail_on: set[str] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default = default_response or AgentResponse(answer="ok")
        self._fail_on = fail_on or set()
        self._call_log: list[tuple[str, str | None]] = []
        self._session_counter = 0

    async def execute(self, message: str, *, session_id: str | None = None) -> AgentResponse:
        self._call_log.append((message, session_id))
        if message in self._fail_on:
            msg = f"Simulated failure for: {message}"
            raise RuntimeError(msg)
        return self._responses.get(message, self._default)

    async def create_session(self) -> str:
        self._session_counter += 1
        return f"session-{self._session_counter}"

    def get_sandbox_executor(self, session_id: str | None = None) -> CodeExecutor | None:
        return None
