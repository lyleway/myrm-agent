"""The outbound scrub masks host paths in free text, and leaves the folders an approval card offers to grant exact."""

from __future__ import annotations

import copy

import pytest
from myrm_agent_harness.toolkits.code_execution.executors.models import scrub_sensitive_info

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


def test_free_text_of_the_same_event_is_still_masked() -> None:
    scrubbed = scrub_outbound_event(_approval_event(_HOME_FOLDER))
    (request,) = _requests(scrubbed)
    assert request["args"] == {"path": f"{_MASKED_HOME_FOLDER}/secret.txt"}
    assert request["description"] == f"Read {_MASKED_HOME_FOLDER}/secret.txt"
    assert isinstance(scrubbed, dict)
    assert scrubbed["messageId"] == "m1"


def test_a_folder_plan_the_model_writes_into_the_arguments_is_still_masked() -> None:
    event = _approval_event(_HOME_FOLDER)
    (request,) = _requests(event)
    request["args"] = {"grantDirectories": [{"path": _HOME_FOLDER}]}
    (scrubbed,) = _requests(scrub_outbound_event(event))
    assert scrubbed["args"] == {"grantDirectories": [{"path": _MASKED_HOME_FOLDER}]}


def test_the_same_key_in_any_other_event_is_still_masked() -> None:
    event = {"type": "tool_start", "data": {"args": {"grantDirectories": [{"path": _HOME_FOLDER}]}}}
    assert scrub_outbound_event(event) == {
        "type": "tool_start",
        "data": {"args": {"grantDirectories": [{"path": _MASKED_HOME_FOLDER}]}},
    }


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
async def test_the_gateway_sends_the_folder_exact_and_the_args_masked() -> None:
    gateway = AgentGateway(
        GatewayConfig(max_global=10, max_per_user=3, queue_timeout=2.0, execution_timeout=5.0, execution_hard_ceiling=60.0)
    )

    async def stream():
        yield _approval_event(_HOME_FOLDER)

    events = [event async for event in gateway.execute_stream(stream(), agent_type="test")]

    assert len(events) == 1
    (request,) = _requests(events[0])
    assert request["grantDirectories"] == [{"path": _HOME_FOLDER, "writable": False}]
    assert request["args"] == {"path": f"{_MASKED_HOME_FOLDER}/secret.txt"}
