"""A business failure that reports itself as transient is retried once, and not at all once the request was stopped.

A tool can answer at the transport level and still report ``429`` / "too many requests" in its payload; the executor
treats that as retryable and runs the call one more time after a backoff. That retry is a second way to run a call the
user already stopped, so the stop guard has to hold on this path as well as on the exception path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import ToolMessage

from myrm_agent_harness.agent.errors.tool_error_category import ToolErrorCategory
from myrm_agent_harness.agent.middlewares.tooling import tool_executor
from myrm_agent_harness.agent.middlewares.tooling.tool_executor import execute_with_retry
from myrm_agent_harness.utils.runtime.cancellation import CancellationToken, set_cancel_token

# ``web_fetch`` and the file tools are exempt from sniffing, so the failures are reported by a tool that is not.
_TOOL = "mcp__crm__lookup"
_TRANSIENT = '{"success": false, "code": 429, "message": "too many requests"}'
_PERMANENT = '{"success": false, "code": 404, "message": "not found"}'


def _request() -> MagicMock:
    request = MagicMock()
    request.tool_call = {"name": _TOOL, "id": "tc_1"}
    return request


class _Replies:
    """Answers the n-th call with the n-th reply; ``on_first_call`` runs while the first call is in flight."""

    def __init__(self, *replies: str, on_first_call: Callable[[], None] | None = None) -> None:
        self.replies = replies
        self.on_first_call = on_first_call
        self.calls = 0

    async def __call__(self, _request: object) -> ToolMessage:
        self.calls += 1
        if self.calls == 1 and self.on_first_call is not None:
            self.on_first_call()
        return ToolMessage(content=self.replies[self.calls - 1], name=_TOOL, tool_call_id="tc_1")


@pytest.fixture
def event_logger() -> Iterator[MagicMock]:
    logger = MagicMock()
    logger.log = AsyncMock()
    with (
        patch.object(tool_executor, "get_event_logger", return_value=logger),
        patch.object(tool_executor, "get_terminal_errors", return_value=set()),
    ):
        yield logger


@pytest.fixture
def token() -> Iterator[CancellationToken]:
    request_token = CancellationToken("req-1")
    set_cancel_token(request_token)
    yield request_token
    set_cancel_token(None)


@pytest.mark.asyncio
async def test_a_transient_business_failure_is_retried_once_after_a_backoff(event_logger: MagicMock) -> None:
    handler = _Replies(_TRANSIENT, "ok")

    with patch.object(tool_executor, "_backoff_sleep", new=AsyncMock()) as backoff:
        result = await execute_with_retry(_request(), handler, _TOOL, "tc_1", allowed_domains=None)

    assert handler.calls == 2
    assert isinstance(result, ToolMessage)
    assert result.content == "ok"
    backoff.assert_awaited_once()
    # 2 ** attempt plus up to one second of jitter.
    assert 1.0 <= backoff.await_args.args[0] <= 2.0
    event_logger.log.assert_awaited_once_with(
        "TOOL_RETRY",
        {"tool_name": _TOOL, "attempt": 2, "reason": "semantic_retryable:Explicit failure flag 'success=False'"},
    )


@pytest.mark.asyncio
async def test_a_second_transient_failure_is_returned_not_retried_again(event_logger: MagicMock) -> None:
    handler = _Replies(_TRANSIENT, _TRANSIENT)

    with patch.object(tool_executor, "_backoff_sleep", new=AsyncMock()):
        result = await execute_with_retry(_request(), handler, _TOOL, "tc_1", allowed_domains=None)

    assert handler.calls == 2, "a call gets at most one retry"
    assert isinstance(result, ToolMessage)
    assert result.content == _TRANSIENT


@pytest.mark.asyncio
async def test_a_permanent_business_failure_is_returned_without_a_retry(event_logger: MagicMock) -> None:
    handler = _Replies(_PERMANENT)

    result = await execute_with_retry(_request(), handler, _TOOL, "tc_1", allowed_domains=None)

    assert handler.calls == 1
    assert isinstance(result, ToolMessage)
    assert result.content == _PERMANENT
    event_logger.log.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_transient_failure_that_comes_back_after_the_stop_is_not_retried(
    token: CancellationToken, event_logger: MagicMock
) -> None:
    handler = _Replies(_TRANSIENT, "ok", on_first_call=token.cancel)

    result = await execute_with_retry(_request(), handler, _TOOL, "tc_1", allowed_domains=None)

    assert handler.calls == 1, "the retry would run again a call the user already stopped"
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.additional_kwargs["error_category"] == ToolErrorCategory.TOOL_CANCELLED
    assert "not retried" in str(result.content)
