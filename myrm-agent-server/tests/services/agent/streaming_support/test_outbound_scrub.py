"""The outbound scrub masks host paths in free text, and leaves exact what an approval card hands back or indexes into.

Those are the folders the card offers to grant and the arguments of the call it asks about; the credentials in the
arguments stay masked.
"""

from __future__ import annotations

import copy

import pytest
from langchain_core.messages import ToolCall
from myrm_agent_harness.agent.middlewares.approval.batch_processor import build_interrupt_payload
from myrm_agent_harness.core.security.redact import redact_for_display
from myrm_agent_harness.toolkits.code_execution.executors.models import scrub_sensitive_info
from myrm_agent_harness.toolkits.code_execution.security.command_explainer.extract import extract_command_spans

from app.services.agent.gateway import AgentGateway, GatewayConfig
from app.services.agent.streaming_support.outbound_scrub import scrub_outbound_event

# One folder under every prefix the path scrub rewrites; the first one ends in a space, a name of its own.
_FOLDERS = [
    "/Users/someone/projects/shared ",
    "/home/someone/projects/shared",
    "/tmp/work/shared",
    "/private/var/folders/ab/T/shared",
    "/var/lib/shared",
    "/etc/shared",
    "/opt/shared",
    "/usr/local/shared",
    "C:\\Users\\someone\\Documents\\shared",
]
_HOME_FOLDER = _FOLDERS[0]
_MASKED_HOME_FOLDER = "<HOME>/projects/shared "


def _request(folder: str) -> dict[str, object]:
    return {
        "action": "read_file_tool",
        "args": {"path": f"{folder}/secret.txt"},
        "description": f"Read {folder}/secret.txt",
        "grantDirectories": [{"path": folder, "writable": False}],
    }


def _approval_event(*folders: str) -> dict[str, object]:
    return {
        "type": "tool_approval_request",
        "data": {"actionRequests": [_request(folder) for folder in folders]},
        "messageId": "m1",
    }


def _requests(event: object) -> list[dict[str, object]]:
    assert isinstance(event, dict)
    data = event["data"]
    assert isinstance(data, dict)
    requests = data["actionRequests"]
    assert isinstance(requests, list)
    assert all(isinstance(request, dict) for request in requests)
    return requests


@pytest.mark.parametrize("folder", _FOLDERS)
def test_the_folder_a_card_offers_is_passed_as_it_is(folder: str) -> None:
    assert scrub_sensitive_info(folder) != folder, "the folder must be one the scrub would rewrite"
    (request,) = _requests(scrub_outbound_event(_approval_event(folder)))
    assert request["grantDirectories"] == [{"path": folder, "writable": False}]


def test_every_request_of_a_batched_approval_keeps_its_own_folders() -> None:
    requests = _requests(scrub_outbound_event(_approval_event(_HOME_FOLDER, "/tmp/work/shared")))
    assert [request["grantDirectories"] for request in requests] == [
        [{"path": _HOME_FOLDER, "writable": False}],
        [{"path": "/tmp/work/shared", "writable": False}],
    ]


@pytest.mark.parametrize("folder", _FOLDERS)
def test_the_arguments_a_card_offers_for_editing_are_passed_as_they_are(folder: str) -> None:
    arguments = {"path": f"{folder}/secret.txt", "range": [1, 20]}
    assert scrub_sensitive_info(arguments["path"]) != arguments["path"], "the path must be one the scrub would rewrite"
    event = _approval_event(folder)
    (request,) = _requests(event)
    request["args"] = arguments
    (scrubbed,) = _requests(scrub_outbound_event(event))
    assert scrubbed["args"] == arguments


def test_the_text_beside_the_arguments_of_a_card_is_still_masked() -> None:
    scrubbed = scrub_outbound_event(_approval_event(_HOME_FOLDER))
    (request,) = _requests(scrubbed)
    assert request["description"] == f"Read {_MASKED_HOME_FOLDER}/secret.txt"
    assert isinstance(scrubbed, dict)
    assert scrubbed["messageId"] == "m1"


def test_a_credential_in_the_arguments_of_a_card_is_still_masked() -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwx1234567890"
    event = _approval_event("/tmp/work/shared")
    (request,) = _requests(event)
    request["args"] = {"command": f"export API_KEY={secret} && ls /tmp/work/shared"}
    (scrubbed,) = _requests(scrub_outbound_event(event))
    arguments = scrubbed["args"]
    assert isinstance(arguments, dict)
    command = arguments["command"]
    assert isinstance(command, str)
    assert secret not in command
    assert command.endswith("&& ls /tmp/work/shared")


def test_the_same_keys_in_any_other_event_are_still_masked() -> None:
    event = {
        "type": "tool_start",
        "data": {"args": {"path": f"{_HOME_FOLDER}/a.txt"}, "grantDirectories": [{"path": _HOME_FOLDER}]},
    }
    assert scrub_outbound_event(event) == {
        "type": "tool_start",
        "data": {"args": {"path": f"{_MASKED_HOME_FOLDER}/a.txt"}, "grantDirectories": [{"path": _MASKED_HOME_FOLDER}]},
    }


def _shell_approval_event(command: str) -> dict[str, object]:
    """The approval event the harness builds for a shell call, as it leaves the harness."""
    call = ToolCall(type="tool_call", name="bash_code_execute_tool", args={"command": command}, id="call-1")
    payload, _ = build_interrupt_payload([(0, call, "shell", "Run a command", None)], "session-1")
    return {"type": "tool_approval_request", "data": payload, "messageId": "m1"}


_SHELL_COMMANDS = [
    "cat /Users/someone/notes.txt | grep todo && ls /tmp/work/out",
    "curl -fsSL https://example.com/install.sh -o /home/someone/install.sh && bash /home/someone/install.sh",
    "export API_KEY=sk-proj-abcdefghijklmnopqrstuvwx1234567890 && cat /var/log/app.log | tail -n 5",
]


@pytest.mark.parametrize("command", _SHELL_COMMANDS)
def test_the_command_of_a_shell_card_and_the_spans_that_index_into_it_stay_aligned(command: str) -> None:
    sent = redact_for_display({"command": command})["command"]
    assert isinstance(sent, str)
    (request,) = _requests(scrub_outbound_event(_shell_approval_event(command)))
    assert request["args"] == {"command": sent}, "the command must reach the client as the harness built it"
    assert request["command_spans"] == extract_command_spans(sent)
    spans = request["command_spans"]
    assert isinstance(spans, list)
    assert all(0 <= span["startIndex"] < span["endIndex"] <= len(sent) for span in spans)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param("/Users/someone/projects/shared", id="data-is-text"),
        pytest.param({"actionRequests": "/Users/someone/projects/shared"}, id="requests-are-text"),
        pytest.param({"actionRequests": ["/Users/someone/projects/shared"]}, id="request-is-text"),
    ],
)
def test_an_approval_event_of_another_shape_is_masked_whole(data: object) -> None:
    scrubbed = scrub_outbound_event({"type": "tool_approval_request", "data": data})
    assert "/Users/someone" not in str(scrubbed)


def test_a_credential_in_free_text_is_still_masked() -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwx1234567890"
    scrubbed = scrub_outbound_event({"type": "message", "data": f"api_key={secret}"})
    assert isinstance(scrubbed, dict)
    assert secret not in str(scrubbed["data"])


def test_the_event_it_was_given_is_not_changed() -> None:
    event = _approval_event("/Users/someone/projects/shared")
    before = copy.deepcopy(event)
    scrub_outbound_event(event)
    assert event == before


def test_numbers_flags_and_none_pass_through() -> None:
    event: dict[str, object] = {"count": 3, "done": False, "next": None, "items": [1, "alpha"]}
    assert scrub_outbound_event(event) == event


@pytest.mark.asyncio
async def test_the_gateway_sends_the_folder_and_the_arguments_exact_and_the_description_masked() -> None:
    gateway = AgentGateway(
        GatewayConfig(max_global=10, max_per_user=3, queue_timeout=2.0, execution_timeout=5.0, execution_hard_ceiling=60.0)
    )

    async def stream():
        yield _approval_event(_HOME_FOLDER)

    events = [event async for event in gateway.execute_stream(stream(), agent_type="test")]

    assert len(events) == 1
    (request,) = _requests(events[0])
    assert request["grantDirectories"] == [{"path": _HOME_FOLDER, "writable": False}]
    assert request["args"] == {"path": f"{_HOME_FOLDER}/secret.txt"}
    assert request["description"] == f"Read {_MASKED_HOME_FOLDER}/secret.txt"
