"""A /learn authoring turn: what the request's security overlays leave of ``skill_manage``."""

from __future__ import annotations

import pytest
from myrm_agent_harness.agent.security.types import PermissionAction

from app.core.channel_bridge.learn_handler import learn_authoring_prompt_text
from app.core.security.session_overlay import SecurityOverlaySpec
from tests.support.overlay_verdict import verdict_under

ALLOW, ASK, DENY = PermissionAction.ALLOW, PermissionAction.ASK, PermissionAction.DENY

_LEARN_PROMPT = '[/learn] The user wants you to learn a reusable skill\n3. Save it with the `skill_manage_tool` (action="save").'
_Call = tuple[str, dict[str, object]]

_SKILL: _Call = ("skill_manage_tool", {"action": "save", "name": "s"})
_SAVED_ASK: dict[str, object] = {"permissions": {"*": "allow", "skill_manage": "ask"}}


def test_a_learn_turn_under_the_explore_preset_can_save_a_skill() -> None:
    explore = SecurityOverlaySpec(preset="explore")
    learn = SecurityOverlaySpec(preset="explore", learn_authoring=True)

    assert verdict_under(explore.apply(_SAVED_ASK), *_SKILL) is DENY
    assert verdict_under(learn.apply(_SAVED_ASK), *_SKILL) is ASK


def test_a_learn_turn_under_explore_still_denies_every_other_write() -> None:
    learn = SecurityOverlaySpec(preset="explore", learn_authoring=True)
    config = learn.apply(_SAVED_ASK)

    writes: list[_Call] = [
        ("file_write_tool", {"path": "/tmp/a.md", "content": "x"}),
        ("bash_code_execute_tool", {"command": "rm -rf build"}),
        ("memory_save_tool", {}),
        ("skill_market_tool", {"action": "install", "skill_id": "s"}),
    ]
    for call in writes:
        assert verdict_under(config, *call) is DENY, call[0]


@pytest.mark.parametrize("preset", [None, "explore", "accept_edits"])
def test_a_learn_turn_never_reopens_a_saved_denial_of_skill_manage(preset: str | None) -> None:
    saved: dict[str, object] = {"permissions": {"skill_manage": "deny"}}
    learn = SecurityOverlaySpec(preset=preset, learn_authoring=True)

    assert verdict_under(learn.apply(saved), *_SKILL) is DENY


def test_a_learn_turn_does_not_lift_the_remote_denial_of_skill_manage() -> None:
    learn = SecurityOverlaySpec(preset="explore", trust_zone="remote_exposed", admission_path="tunnel", learn_authoring=True)

    assert verdict_under(learn.apply(_SAVED_ASK), *_SKILL) is DENY


def test_a_learn_turn_under_accept_edits_still_asks_before_saving() -> None:
    learn = SecurityOverlaySpec(preset="accept_edits", learn_authoring=True)

    assert verdict_under(learn.apply({"permissions": {"skill_manage": "allow"}}), *_SKILL) is ASK


def test_a_regular_turn_applies_nothing() -> None:
    config: dict[str, object] = {"permissions": {"skill_manage": "deny"}}
    assert SecurityOverlaySpec().apply(config) is config


def test_learn_authoring_prompt_text_accepts_multimodal_blocks() -> None:
    query: list[dict[str, str]] = [
        {"type": "text", "text": _LEARN_PROMPT},
    ]
    assert learn_authoring_prompt_text(query) == _LEARN_PROMPT
