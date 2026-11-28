"""Every system event DTO that offers ``to_dict`` can build its payload.

A ``to_dict`` that reads an attribute its class does not have fails only when it is first
called, which may be never. So each DTO that offers one has to prove it with a working sample.
"""

from __future__ import annotations

import json
from typing import Protocol

import pytest

from myrm_agent_harness.runtime.events import system_events
from myrm_agent_harness.runtime.events.system_events import (
    DelegationPolicyDecision,
    JsonObject,
    LocatorSelfHealedEvent,
    MCPAuthExpiredEvent,
    SubagentLifecycleData,
)


class _HasToDict(Protocol):
    def to_dict(self) -> JsonObject: ...


_POLICY = DelegationPolicyDecision(
    allowed=False,
    reason="depth exhausted",
    requested_role="worker",
    effective_scope="none",
)

# One working sample per DTO that defines ``to_dict``; the lifecycle data carries a policy so
# that the nested payload is built too.
_SAMPLES: dict[str, _HasToDict] = {
    "DelegationPolicyDecision": _POLICY,
    "SubagentLifecycleData": SubagentLifecycleData(agent_type="research", status="denied", policy=_POLICY),
    "MCPAuthExpiredEvent": MCPAuthExpiredEvent(server_name="天气", error_detail="401 Unauthorized"),
    "LocatorSelfHealedEvent": LocatorSelfHealedEvent(
        ref="e12",
        old_name="Buy",
        new_name="Buy now",
        url="https://example.com/cart",
        role="button",
        distance=0.2,
    ),
}


def _dtos_with_to_dict() -> set[str]:
    return {
        name
        for name, obj in vars(system_events).items()
        if isinstance(obj, type) and obj.__module__ == system_events.__name__ and "to_dict" in vars(obj)
    }


def test_every_dto_that_defines_to_dict_has_a_sample_here() -> None:
    assert _dtos_with_to_dict() == set(_SAMPLES)


@pytest.mark.parametrize("name", sorted(_SAMPLES))
def test_the_payload_of_a_sample_is_json(name: str) -> None:
    payload = _SAMPLES[name].to_dict()

    assert json.loads(json.dumps(payload, allow_nan=False)) == payload
