"""User-session credential context and the clarification error of human-in-the-loop tools.

[INPUT]
- (none — pure types, zero dependencies)

[OUTPUT]
- EphemeralUserCredential / user_credentials_ctx: a user-bound credential carried by the coroutine context
- with_user_credentials() / propagate_user_credentials(): bind the credentials to a block or to a function run elsewhere
- ToolClarificationError: raised when a tool needs the person to clarify before it can proceed

[POS]
Ephemeral, context-bound credentials that reach tool execution without touching the database or the environment.
Imports nothing from the security subsystem.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from typing import ParamSpec, TypeVar, cast, overload


@dataclass(frozen=True, slots=True)
class EphemeralUserCredential:
    """User-session-affinity credential context for enterprise integrations and SaaS.

    Ephemerally bound to the active coroutine context using ContextVar. Passed down
    from the business layer (myrm-agent-server) to propagate user OAuth tokens into
    tool execution paths (like sub-processes or HTTP requests) without storing them
    in the database or global system environment variables.
    """

    issuer: str  # Identifies the system/provider (e.g. "feishu", "dingtalk", "github")
    token: str  # Ephemeral Token (OAuth user_access_token or PAT)
    scope: str = ""  # Authorized Scope of the token
    user_id: str = ""  # External system user identifier
    expires_at: float | None = None  # Ephemeral expiry time in monotonic/epoch seconds
    refresh_callback: Callable[[], Awaitable[EphemeralUserCredential | None]] | None = (
        None  # Optional callback to refresh expired token
    )


# Session-bound user credentials context variable. Managed per coroutine.
user_credentials_ctx: ContextVar[tuple[EphemeralUserCredential, ...]] = ContextVar("user_credentials_ctx", default=())


@asynccontextmanager
async def with_user_credentials(
    credentials: tuple[EphemeralUserCredential, ...],
) -> AsyncIterator[None]:
    """Async context manager to safely inject and automatically clean up user credentials.

    Ensures that credentials are bound only to the specific async execution block.
    """
    token_ctx = user_credentials_ctx.set(credentials)
    try:
        yield
    finally:
        user_credentials_ctx.reset(token_ctx)


_P = ParamSpec("_P")
_R = TypeVar("_R")


@overload
def propagate_user_credentials(  # noqa: UP047
    fn: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]: ...


@overload
def propagate_user_credentials(  # noqa: UP047
    fn: Callable[_P, _R],
) -> Callable[_P, _R]: ...


def propagate_user_credentials(fn: Callable[_P, _R]) -> Callable[_P, _R]:  # noqa: UP047
    """Capture the current context (including user_credentials_ctx) and return a wrapped function.

    When called from any other thread, background worker, or pool, the wrapped function
    executes inside the captured context, ensuring no user credentials are lost.
    """
    try:
        credentials = user_credentials_ctx.get()
    except LookupError:
        credentials = ()

    if inspect.iscoroutinefunction(fn):

        async def async_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            token_ctx = user_credentials_ctx.set(credentials)
            try:
                return cast("_R", await fn(*args, **kwargs))
            finally:
                user_credentials_ctx.reset(token_ctx)

        return cast("Callable[_P, _R]", async_wrapper)

    ctx = copy_context()

    def sync_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        return ctx.run(fn, *args, **kwargs)

    return sync_wrapper


class ToolClarificationError(Exception):
    """Raised when a tool needs human clarification before proceeding.

    Used by HITL (Human-in-the-Loop) tools that require the user to
    disambiguate or confirm parameters (e.g., selecting exact user IDs
    from ambiguous names).
    """
