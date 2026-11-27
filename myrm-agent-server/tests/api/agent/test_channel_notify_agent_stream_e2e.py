"""Live-server E2E: agent-stream holds channel_notify_tool for the person's approval, then delivers to chat.

A send to another chat is socially irreversible, so the stream stops at an approval request and nothing reaches the
recipient. Approving the held call resumes the stream and the message lands in the recipient chat; rejecting it ends
the turn and the recipient chat stays empty.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Iterator

import httpx
import pytest

from tests.support.bash_compressor_e2e import resolve_working_base_selection

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8080")
_E2E_TIMEOUT = httpx.Timeout(180.0)
NOTIFY_BODY = "E2E agent-stream integration test"

_skip_e2e = pytest.mark.skipif(
    not os.getenv("RUN_E2E_TESTS"),
    reason="Set RUN_E2E_TESTS=1 to run end-to-end tests against live server",
)


def _e2e_request(method: str, url: str, *, json: dict[str, object] | None = None) -> httpx.Response:
    with httpx.Client(trust_env=False, timeout=_E2E_TIMEOUT) as client:
        return client.request(method, url, json=json)


def _collect_agent_stream(payload: dict[str, object]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    with httpx.Client(trust_env=False, timeout=_E2E_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{BASE_URL}/api/v1/agents/agent-stream",
            json=payload,
            headers={"Accept": "text/event-stream"},
        ) as response:
            if response.status_code != 200:
                body = response.read().decode("utf-8", errors="replace")
                raise AssertionError(f"agent-stream failed: {response.status_code} {body}")
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                raw = line[6:]
                if raw == "[DONE]":
                    break
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    events.append(parsed)
    return events


def _invoked_tool_names(events: list[dict[str, object]]) -> set[str]:
    names: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") not in {"tasks_steps", "tool_start", "tool_end"}:
            continue
        tool_name = event.get("tool_name")
        if isinstance(tool_name, str) and tool_name:
            names.add(tool_name)
    return names


def _held_actions(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """The action requests of every approval the stream stopped at."""
    actions: list[dict[str, object]] = []
    for event in events:
        data = event.get("data")
        if event.get("type") == "tool_approval_request" and isinstance(data, dict):
            requested = data.get("actionRequests")
            if isinstance(requested, list):
                actions.extend(item for item in requested if isinstance(item, dict))
    return actions


def _delivered_messages(recipient_chat_id: str) -> list[dict[str, object]]:
    """The notify messages stored in the recipient chat; the chat does not exist until a delivery creates it."""
    response = _e2e_request("GET", f"{BASE_URL}/api/v1/chats/{recipient_chat_id}/messages")
    if response.status_code == 404:
        return []
    assert response.status_code == 200, response.text
    messages = response.json().get("data", {}).get("messages", [])
    return [
        msg for msg in messages if isinstance(msg, dict) and isinstance(msg.get("content"), str) and NOTIFY_BODY in msg["content"]
    ]


@contextlib.contextmanager
def _held_send() -> Iterator[tuple[dict[str, object], str]]:
    """An agent whose notify target is a fresh chat, run until its channel_notify_tool call is held for approval.

    Yields the stream payload (re-post it with a resume value to decide the held call) and the recipient chat id.
    """
    model_selection = resolve_working_base_selection(backend_url=BASE_URL)

    recipient_chat_id = f"notify_stream_{uuid.uuid4().hex[:8]}"
    agent_resp = _e2e_request(
        "POST",
        f"{BASE_URL}/api/v1/user-agents",
        json={
            "name": "Channel Notify Stream Live E2E",
            "system_prompt": "You send notifications when asked.",
            "notify_targets": [
                {"channel": "chat", "recipient_id": recipient_chat_id, "label": "Stream E2E"},
            ],
        },
    )
    assert agent_resp.status_code == 200, agent_resp.text
    agent_id = agent_resp.json()["data"]["id"]

    chat_id = f"stream_src_{uuid.uuid4().hex[:8]}"
    assert _e2e_request("POST", f"{BASE_URL}/api/v1/chats/", json={"chat_id": chat_id}).status_code == 200

    query = (
        "Call channel_notify_tool exactly once. "
        f'Send body "{NOTIFY_BODY}" to the configured chat target. '
        "Do not call any other tools. After success, reply NOTIFY_DONE."
    )

    try:
        payload: dict[str, object] = {
            "message_id": f"msg_{uuid.uuid4().hex[:8]}",
            "chat_id": chat_id,
            "query": query,
            "action_mode": "agent",
            "agent_id": agent_id,
            "model_selection": model_selection,
            "enable_memory": False,
        }
        held = _collect_agent_stream(payload)
        assert "channel_notify_tool" in _invoked_tool_names(held)
        actions = _held_actions(held)
        assert [action.get("action") for action in actions] == ["channel_notify_tool"], actions
        assert actions[0].get("sociallyIrreversible") is True, actions[0]
        assert not _delivered_messages(recipient_chat_id), "the send reached the chat before anyone approved it"
        yield payload, recipient_chat_id
    finally:
        _e2e_request("DELETE", f"{BASE_URL}/api/v1/user-agents/{agent_id}")


@_skip_e2e
@pytest.mark.e2e
class TestChannelNotifyAgentStreamLive:
    def test_agent_stream_channel_notify_delivers_to_chat(self) -> None:
        """Real agent-stream on a live server: channel_notify_tool is held for approval, then writes to the chat."""
        with _held_send() as (payload, recipient_chat_id):
            resumed = _collect_agent_stream(dict(payload, resumeValue={"decisions": [{"type": "approve"}]}))
            assert not _held_actions(resumed), "the approved send was held again"
            delivered = _delivered_messages(recipient_chat_id)
            assert delivered, f"Expected delivery in {recipient_chat_id} after the approval"

    def test_agent_stream_rejected_channel_notify_never_reaches_the_chat(self) -> None:
        """Rejecting the held call ends the turn without a delivery: the recipient chat stays empty."""
        with _held_send() as (payload, recipient_chat_id):
            resumed = _collect_agent_stream(dict(payload, resumeValue={"decisions": [{"type": "reject"}]}))
            delivered = _delivered_messages(recipient_chat_id)
            assert not delivered, f"a rejected send reached {recipient_chat_id}: {delivered}"
            print(f"E2E_NOTIFY_REJECTED: event types={sorted({str(event.get('type')) for event in resumed})}", flush=True)
