"""Driving a login session the way a user does: wait for the link, then paste the code.

[OUTPUT]
- drive_login: Run a login session to its end, pasting a code once the CLI has shown its link.

[POS]
Test support of the login session tests, the stand-in ones and the real-binary ones.
"""

from __future__ import annotations

from myrm_agent_harness.toolkits.acp.auth import AuthEvent, AuthEventType, CliLoginSession


async def drive_login(session: CliLoginSession, code: str | None) -> list[AuthEvent]:
    """Every event of ``session`` to its end; ``code`` is pasted once, when the first link is shown."""
    events: list[AuthEvent] = []
    pasted = False
    async for event in session.run():
        events.append(event)
        if code is not None and not pasted and event.type is AuthEventType.PROMPT and event.url:
            pasted = True
            await session.feed(code)
    return events
