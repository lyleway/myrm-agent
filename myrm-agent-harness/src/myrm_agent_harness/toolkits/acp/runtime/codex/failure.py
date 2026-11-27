"""Failure classification of a Codex turn, from the structured error the app server attaches to it.

[INPUT]
- toolkits.acp.runtime.event_text::condense_failure_text (POS: Text shaping of the events every runtime emits)
- toolkits.acp.runtime.codex.wire::Json, as_int, as_obj, as_str (POS: Typed reads of the decoded JSON that Codex's app server sends)
- toolkits.acp.types::AcpError, AcpErrorCode (POS: ACP runtime type definitions layer.)

[OUTPUT]
- is_auth_rejection: whether a turn error says the model API refused the login
- classify_turn_error: the ``AcpError`` of a failed turn

[POS]
Error vocabulary of CodexRuntime. The server names what went wrong (``codexErrorInfo``: a category, or a category with
the HTTP status that caused it), so no failure is guessed from its wording. Messages are Codex's own text, never advice
about product settings.
"""

from __future__ import annotations

from myrm_agent_harness.toolkits.acp.runtime.codex.wire import Json, as_int, as_obj, as_str
from myrm_agent_harness.toolkits.acp.runtime.event_text import condense_failure_text
from myrm_agent_harness.toolkits.acp.types import AcpError, AcpErrorCode

_UNAUTHORIZED = 401
_RATE_LIMITED = 429
_SERVER_ERROR = 500
# Categories of a failure that a later attempt can succeed at.
_TRANSIENT = frozenset({"serverOverloaded", "internalServerError"})


def _category(error: Json) -> tuple[str, int | None]:
    """The category of a turn error and the HTTP status behind it, when the server names one.

    ``codexErrorInfo`` is either the category itself or an object with the category as its only key, holding what
    the server knows about it (``{"responseStreamDisconnected": {"httpStatusCode": 401}}``).
    """
    info = error.get("codexErrorInfo")
    if isinstance(info, str):
        return info, None
    if isinstance(info, dict) and info:
        name, detail = next(iter(info.items()))
        status = as_obj(detail).get("httpStatusCode")
        return name, as_int(status) or None
    return "", None


def is_auth_rejection(error: Json) -> bool:
    """Whether ``error`` says the model API refused the login (a 403 does not: a blocked region answers it too)."""
    name, status = _category(error)
    return name == "unauthorized" or status == _UNAUTHORIZED


def classify_turn_error(error: Json) -> AcpError:
    """The error of a failed turn: its category and status decide the code, Codex's own words the message."""
    name, status = _category(error)
    message = condense_failure_text(as_str(error.get("message"))) or "The turn failed"
    if is_auth_rejection(error):
        return AcpError(AcpErrorCode.AUTH_FAILED, message)
    if name == "rateLimitExceeded" or status == _RATE_LIMITED:
        return AcpError(AcpErrorCode.RATE_LIMITED, message, retryable=True)
    transient = name in _TRANSIENT or (status is not None and status >= _SERVER_ERROR)
    return AcpError(AcpErrorCode.UNKNOWN, message, retryable=transient)
