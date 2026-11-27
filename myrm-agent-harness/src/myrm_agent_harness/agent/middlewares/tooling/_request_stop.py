"""A stopped request starts no further tool call.

[INPUT]
- agent.errors.tool_error_category::ToolErrorCategory (POS: Canonical tool error categories for structured error classification.)
- agent.middlewares.tooling._tool_helpers::make_error_msg (POS: Stateless helper functions)
- utils.runtime.cancellation::get_cancel_token (POS: request-level cancellation token carried in a ContextVar)

[OUTPUT]
- request_stopped(): whether the request the calling tool belongs to has been stopped
- check_request_stopped(): the result of a call refused because its request was stopped before it started, else None

[POS]
Pre-call guard of the tool interceptor, and the one stop predicate its executor shares: a call that has not started when
its request is stopped (the user's Stop, the request timeout, a client that disconnected), such as one parked at the
conflict gate behind a call the stop just cut, is refused, so a stopped request never changes a file or reaches a remote
service through a call that was only queued. The refusal comes right after the E-Stop check, which keeps its own audited
refusal, and before the loop guard or the turn budget records the call.
"""

from __future__ import annotations

from langchain_core.messages import ToolMessage

from myrm_agent_harness.agent.errors.tool_error_category import ToolErrorCategory
from myrm_agent_harness.agent.middlewares.tooling._tool_helpers import make_error_msg
from myrm_agent_harness.utils.runtime.cancellation import get_cancel_token


def request_stopped() -> bool:
    """Whether the request the calling tool belongs to has been stopped."""
    token = get_cancel_token()
    return token is not None and token.is_cancelled


def check_request_stopped(tool_name: str, tool_call_id: str) -> ToolMessage | None:
    """The result of a call that must not start because its request was stopped, or ``None`` while the request runs."""
    if not request_stopped():
        return None
    return make_error_msg(
        tool_name,
        tool_call_id,
        f"{tool_name} was not started: the request was stopped first.",
        error_category=ToolErrorCategory.TOOL_CANCELLED,
    )
