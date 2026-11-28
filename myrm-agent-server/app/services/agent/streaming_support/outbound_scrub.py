"""Outbound event scrub: the last text filter before an agent event leaves the server.

[INPUT]
- myrm_agent_harness.core.events.types::AgentEventType (POS: the wire name of the approval request event)
- myrm_agent_harness.core.security.redact::redact_sensitive_text (POS: masks credentials in one text)
- myrm_agent_harness.toolkits.code_execution.executors.models::scrub_sensitive_info (POS: masks host paths and credentials in one text)

[OUTPUT]
- scrub_outbound_event: the event with host paths and credentials masked in every string it holds; an approval request keeps the host paths of its folder plan and of its tool arguments, whose credentials are still masked

[POS]
Applied by the gateway to every event of a run. A string is free text, so it is masked. Two things on an approval card are
data, not text. The folders the card offers to grant are read back exactly, and the server opens the folder it is handed.
The arguments of the call are edited in place and sent back as the call to run, and the command highlights of a shell
card (``command_spans``) index into the command by character position. A host path rewritten to ``<HOME>/x`` in either
names a folder or file that does not exist, or moves every highlight after it. The harness has already masked the
credentials in those arguments for display; masking them again here is a no-op on that text and keeps the last filter
a credential filter for every string.
"""

from __future__ import annotations

from collections.abc import Callable

from myrm_agent_harness.core.events.types import AgentEventType
from myrm_agent_harness.core.security.redact import redact_sensitive_text
from myrm_agent_harness.toolkits.code_execution.executors.models import scrub_sensitive_info

_APPROVAL_REQUEST = AgentEventType.TOOL_APPROVAL_REQUEST.value
# The folders "also grant folder access" would add, planned by the harness beside each action request. The client echoes
# each path back in its decision and the server opens exactly that folder, so a path masked to ``<HOME>/x`` would name a
# folder that does not exist and ticking the box would grant nothing. The roots list shows the same folders unmasked.
_FOLDER_PLAN = "grantDirectories"
# The arguments of the call the card asks about: the user edits them and the card sends them back as the call to run.
_TOOL_ARGS = "args"


def scrub_outbound_event(event: object) -> object:
    """``event`` with host paths and credentials masked in every string, the folder plan and arguments of an approval excepted."""
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
    return {key: _scrub_request_field(key, value) for key, value in request.items()}


def _scrub_request_field(key: str, value: object) -> object:
    """One field of an action request: its folder plan passes as it is, its arguments lose credentials only."""
    if key == _FOLDER_PLAN:
        return value
    if key == _TOOL_ARGS:
        return _mask(value, redact_sensitive_text)
    return _scrub_text(value)


def _scrub_text(data: object) -> object:
    """Mask host paths and credentials in every string under ``data``."""
    return _mask(data, scrub_sensitive_info)


def _mask(data: object, mask_text: Callable[[str], str]) -> object:
    """``data`` with ``mask_text`` applied to every string under it."""
    if isinstance(data, dict):
        return {key: _mask(value, mask_text) for key, value in data.items()}
    if isinstance(data, list):
        return [_mask(item, mask_text) for item in data]
    if isinstance(data, str):
        return mask_text(data)
    return data
