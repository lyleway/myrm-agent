"""The dispatcher turns only the custom events it names into SSE events, and lifts the structured fields of some.

A ``custom`` chunk is whatever a tool wrote to the run's StreamWriter. An event the dispatcher does not name never
reaches the browser, and nothing in the emitting code notices; these cases pin that rule and the payload shapes of the
custom events that have no case of their own in ``test_stream_dispatcher.py``.

[INPUT]
- tests.agent.streaming.custom_chunk::dispatch_custom_chunk (POS: one custom chunk through the real dispatcher)

[OUTPUT]
- Cases: a chunk that is not a mapping, an unnamed event and an unknown event are dropped; an evicted reference given as
  a bare string is wrapped and one without a name is dropped; a PTC notification keeps its structured fields on the
  event next to its payload.
"""

from __future__ import annotations

import pytest

from tests.agent.streaming.custom_chunk import dispatch_custom_chunk

_PTC_STRUCTURED_FIELDS = (
    "level",
    "message",
    "session_id",
    "trace_id",
    "progress",
    "step_index",
    "total_steps",
    "category",
)


@pytest.mark.parametrize(
    "chunk",
    [
        pytest.param("not a mapping", id="not-a-mapping"),
        pytest.param(["plan_update"], id="a-list"),
        pytest.param({"data": {"revision": 1}}, id="no-name"),
        pytest.param({"name": "never_registered", "data": {"revision": 1}}, id="unknown-name"),
    ],
)
async def test_a_custom_chunk_the_dispatcher_does_not_name_is_dropped(chunk: object) -> None:
    assert await dispatch_custom_chunk(chunk) == []


async def test_an_evicted_reference_given_as_a_bare_string_is_wrapped() -> None:
    events = await dispatch_custom_chunk({"name": "tool_evicted_ref", "data": "  stderr_trace.txt  "})

    assert [(event.type, event.data) for event in events] == [("tool_evicted_ref", {"evicted_ref": "stderr_trace.txt"})]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("   ", id="blank-string"),
        pytest.param({"stream": "stderr"}, id="mapping-without-a-reference"),
        pytest.param({"evicted_ref": ""}, id="empty-reference"),
        pytest.param(None, id="no-payload"),
    ],
)
async def test_an_evicted_reference_without_a_name_is_dropped(payload: object) -> None:
    assert await dispatch_custom_chunk({"name": "tool_evicted_ref", "data": payload}) == []


async def test_a_ptc_notification_keeps_its_structured_fields_next_to_its_payload() -> None:
    payload = {
        "level": "warning",
        "message": "Fetched 3 of 4 pages",
        "session_id": "s-1",
        "trace_id": "t-1",
        "progress": 0.75,
        "step_index": 3,
        "total_steps": 4,
        "category": "fetch",
        "raw": {"kept": "only inside data"},
    }

    events = await dispatch_custom_chunk({"name": "ptc_notify", "data": payload})

    assert len(events) == 1
    forwarded = events[0].to_dict()
    assert forwarded["type"] == "ptc_notify"
    assert forwarded["data"] == payload
    assert {field: forwarded[field] for field in _PTC_STRUCTURED_FIELDS} == {
        field: payload[field] for field in _PTC_STRUCTURED_FIELDS
    }
    assert "raw" not in forwarded


async def test_a_ptc_notification_whose_payload_is_not_a_mapping_is_forwarded_as_it_is() -> None:
    events = await dispatch_custom_chunk({"name": "ptc_notify", "data": "Fetching pages"})

    assert [(event.type, event.data) for event in events] == [("ptc_notify", "Fetching pages")]
