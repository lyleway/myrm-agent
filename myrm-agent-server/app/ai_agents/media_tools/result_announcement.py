"""Tells the chat stream what a media tool call produced, so the message shows a card for it.

[INPUT]
- myrm_agent_harness.core.events.types::AgentEventType (POS: SSE event type names)
- myrm_agent_harness.utils.runtime.progress_sink::get_tool_progress_sink (POS: the channel through which a running tool pushes events into the chat stream)

[OUTPUT]
- announce_result(): pushes a tool's result into the chat stream and hands it back for the tool to return

[POS]
The harness streams a tool call's arguments as its Task Steps entry and never the tool result, so a card cannot be read
back from the call: the tool announces its own result as the ``tool_end`` event that the stream collector
(``media_generated.collect_media_generated``) and the chat store of the frontend register the cards from.
The announcement comes last, after the tasks are queued or the media is stored, and a call outside a chat stream has no
channel and announces nothing.
"""

from __future__ import annotations

import logging

from myrm_agent_harness.core.events.types import AgentEventType
from myrm_agent_harness.utils.runtime.progress_sink import get_tool_progress_sink

logger = logging.getLogger(__name__)


async def announce_result(tool_name: str, result: str) -> str:
    """Push ``result`` into the chat stream as the ``tool_end`` of ``tool_name`` and return it unchanged."""
    sink = get_tool_progress_sink()
    if sink is not None:
        try:
            await sink.emit({"type": AgentEventType.TOOL_END.value, "tool_name": tool_name, "result": result})
        except Exception:
            # The work is done and paid for; failing the call here would make the model run it again.
            logger.warning("Failed to announce the result of %s to the stream.", tool_name, exc_info=True)
    return result
