"""Give an E2E page the user activation a real click would give it.

Chrome keeps every ``AudioContext`` suspended until the page has had a user gesture, and the browser orchestrator evaluates
scripts without one, so a page that records or plays sound never gets going. One ``Runtime.evaluate`` flagged as a user gesture,
sent over a DevTools session of its own, sets the page's sticky activation. It lasts until the page navigates, and every context
created after it starts running.
"""

from __future__ import annotations

from chrome_mcp.client import McpPage

from .devtools_session import page_devtools_session

# The expression only reads the flag back: the gesture is what the evaluate carries, and the flag proves the page took it.
_GESTURE_EVALUATE: dict[str, object] = {
    "expression": "navigator.userActivation.hasBeenActive",
    "userGesture": True,
    "returnByValue": True,
}


def grant_user_activation(page: McpPage) -> None:
    """Mark ``page`` as having had a user gesture; grant it again after every navigation or reload."""
    with page_devtools_session(page) as session:
        outcome = session.call("Runtime.evaluate", _GESTURE_EVALUATE).get("result")
    assert isinstance(outcome, dict) and outcome.get("value") is True, f"the page did not take the user activation: {outcome}"
