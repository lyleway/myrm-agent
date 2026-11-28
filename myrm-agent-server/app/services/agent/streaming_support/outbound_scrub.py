"""Outbound event scrub: the last text filter before an agent event leaves the server.

[INPUT]
- myrm_agent_harness.core.events.types::AgentEventType (POS: the wire name of the approval request event)
- myrm_agent_harness.toolkits.code_execution.executors.models::scrub_sensitive_info (POS: masks host paths and credentials in one text)

[OUTPUT]
- scrub_outbound_event: the event with host paths and credentials masked in every string it holds, except the folder plan of an approval request

[POS]
Applied by the gateway to every event of a run. A string is free text, so it is masked; the folders an approval card
offers to grant are data the client reads back exactly, and masking them would hand the server a folder it cannot open.
"""

from __future__ import annotations

from myrm_agent_harness.core.events.types import AgentEventType
from myrm_agent_harness.toolkits.code_execution.executors.models import scrub_sensitive_info

_APPROVAL_REQUEST = AgentEventType.TOOL_APPROVAL_REQUEST.value
# The folders "also grant folder access" would add, planned by the harness beside each action request. The client echoes
# each path back in its decision and the server opens exactly that folder, so a path masked to ``<HOME>/x`` would name a
# folder that does not exist and ticking the box would grant nothing. The roots list shows the same folders unmasked.
# Only this position is exempt: the tool arguments next to it come from the model and stay masked.
_FOLDER_PLAN = "grantDirectories"


def scrub_outbound_event(event: object) -> object:
    """``event`` with host paths and credentials masked in every string, the folder plan of an approval request excepted."""
    if isinstance(event, dict) and event.get("type") == _APPROVAL_REQUEST:
        return {key: _scrub_approval_data(value) if key == "data" else _scrub_text(value) for key, value in event.items()}
    return _scrub_text(event)


def _scrub_approval_data(data: object) -> object:
    if not isinstance(data, dict):
        return _scrub_text(data)
    return {key: _scrub_action_requests(value) if key == "actionRequests" else _scrub_text(value) for key, value in data.items()}


def _scrub_action_requests(requests: object) -> object:
    if not isinstance(requests, list):
        return _scrub_text(requests)
    return [_scrub_action_request(request) for request in requests]


def _scrub_action_request(request: object) -> object:
    if not isinstance(request, dict):
        return _scrub_text(request)
    return {key: value if key == _FOLDER_PLAN else _scrub_text(value) for key, value in request.items()}


def _scrub_text(data: object) -> object:
    """Mask every string under ``data``."""
    if isinstance(data, dict):
        return {key: _scrub_text(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_scrub_text(item) for item in data]
    if isinstance(data, str):
        return scrub_sensitive_info(data)
    return data
