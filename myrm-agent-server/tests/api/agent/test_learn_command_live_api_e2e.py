"""Live-stack API E2E: ``/learn`` saves a skill under the read-only ``explore`` preset, a plain turn cannot.

Task flow (no browser): a custom agent with ``skill_manage_tool`` mounted runs in a chat whose session preset is
``explore``, which restricts every write, ``skill_manage`` included. A plain request to save a skill is refused by the
restriction: the stream reports a policy denial of the tool call and the catalog stays empty. The same chat then gets
a raw ``/learn`` message: the server rewrites it into the authoring prompt, leaves ``skill_manage`` out of the preset's
restriction for that one turn, and the live model saves the skill the user described; the skill lands in the catalog
with the SKILL.md the model sent and the stream announces the committed batch. The preset binds again on the turn
after: a second plain save is refused.

A second flow: the user tells the agent in a plain turn how a release is shipped, then sends a bare ``/learn`` with no
arguments. The server falls back to "the workflow we just went through in this conversation", the live model reads the
conversation back and saves a skill whose SKILL.md carries the step the user named; the stream announces the batch.

A third flow, the same conversation with a lone ``/`` instead: a stray slash is an ordinary message, so nothing is
saved (under the ``explore`` preset a ``/learn`` turn is the only one that could save).

Run::

    ./myrm-dev test -m e2e myrm-agent/myrm-agent-server/tests/api/agent/test_learn_command_live_api_e2e.py
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from tests.api.agent.skill_manage_api_helpers import (
    SAVE_TOOL,
    SKILL_DESCRIPTION,
    announced_batches,
    announced_batches_in_order,
    catalog_skill_id,
    catalog_skills,
    create_agent,
    create_chat,
    data,
    isolated_backend_url,
    requires_live_model,
    skill_md,
    stream_turn,
    user_request,
    wait_for,
    workflow_message,
)

EXPLORE = "explore"


def learn_request(name: str, marker: str) -> str:
    """A raw ``/learn`` message as a user types it: the skill to author, its name, description and one body line."""
    return (
        f"/learn Author one skill: name={name}, description={SKILL_DESCRIPTION}. "
        f"The SKILL.md body is a heading and one line that is exactly: {marker} ."
    )


def stream_digest(events: list[dict[str, object]]) -> str:
    """The stream's error, approval and skill_manage events, shortened: what a turn did instead of saving the skill."""
    shown = [
        json.dumps(event, default=str)[:400]
        for event in events
        if event.get("type") in {"error", "approval_request"} or event.get("tool_name") == SAVE_TOOL
    ]
    return " | ".join(shown[:10]) or f"no such events; event types: {sorted({str(event.get('type')) for event in events})}"


def save_denials(events: list[dict[str, object]]) -> list[str]:
    """Error texts of the skill_manage calls the permission policy refused during a turn."""
    return [
        str(event.get("error"))
        for event in events
        if event.get("tool_name") == SAVE_TOOL and event.get("error_category") == "policy_denied"
    ]


def roll_back(client: httpx.Client, backend_url: str, batches: list[str]) -> None:
    """Undo the committed skill batches a turn announced, newest first (an older batch is refused while a newer one stands)."""
    for batch in reversed(batches):
        rolled_back = client.post(f"{backend_url}/api/v1/skill-optimization/mutations/{batch}/rollback", timeout=60.0)
        assert data(rolled_back).get("status")


@pytest.mark.e2e
@pytest.mark.timeout(900)
@requires_live_model
def test_explore_preset_refuses_a_plain_skill_save_but_lets_learn_save_one() -> None:
    backend_url = isolated_backend_url()
    suffix = uuid.uuid4().hex[:8]
    refused_name, learned_name, later_refused_name = (f"learn-{kind}-{suffix}" for kind in ("refused", "saved", "later"))
    marker = f"LEARN_MARKER_{suffix}"
    chat_id = f"learnexplore{suffix}"

    with httpx.Client() as client:
        agent_id = create_agent(client, backend_url, suffix)
        create_chat(client, backend_url, chat_id, agent_id)

        # A refusal only counts when the model really called the tool and the policy turned the call down:
        # a turn that failed before the model acted would leave the catalog empty as well.
        refused_turn = stream_turn(
            client, backend_url, agent_id, chat_id, user_request(refused_name, marker), security_preset=EXPLORE
        )
        denials = save_denials(refused_turn)
        assert denials, (
            f"the explore preset never refused the plain save with a policy denial; stream: {stream_digest(refused_turn)}"
        )
        # Not "...DENIED": the lane reads an `E2E_*DENIED` line as an infrastructure verdict and would mislabel a red assertion.
        print(f"E2E_PLAIN_SAVE_REFUSED: {denials[0]}", flush=True)
        assert catalog_skill_id(client, backend_url, refused_name) is None, "the explore preset let a plain turn save a skill"
        assert not announced_batches(refused_turn), "a refused save must not announce a committed batch"

        learn_turn = stream_turn(
            client, backend_url, agent_id, chat_id, learn_request(learned_name, marker), security_preset=EXPLORE
        )
        # The learned skill is undone even when an assertion below fails, so a red run leaves the catalog as it found it.
        try:
            assert not save_denials(learn_turn), f"the /learn turn's save was refused; stream: {stream_digest(learn_turn)}"
            skill_id = wait_for(
                lambda: catalog_skill_id(client, backend_url, learned_name),
                timeout_sec=60.0,
                what=f"the /learn turn to save {learned_name} under the explore preset; stream: {stream_digest(learn_turn)}",
            )
            saved = skill_md(client, backend_url, skill_id)
            assert marker in saved, f"the saved SKILL.md lost the marker the user asked for:\n{saved[:600]}"
            batches = announced_batches(learn_turn)
            assert batches, "the /learn turn saved a skill but the stream never announced its committed batch"
            print(f"E2E_LEARN_SAVED: skill={learned_name} batches={sorted(batches)}", flush=True)

            later_turn = stream_turn(
                client, backend_url, agent_id, chat_id, user_request(later_refused_name, marker), security_preset=EXPLORE
            )
            assert save_denials(later_turn), (
                f"the explore preset stopped binding after a /learn turn; stream: {stream_digest(later_turn)}"
            )
            assert catalog_skill_id(client, backend_url, later_refused_name) is None, (
                "the explore preset stopped binding after a /learn turn: a plain turn saved a skill"
            )
            assert not announced_batches(later_turn) - batches, "a refused save must not announce a committed batch"
        finally:
            roll_back(client, backend_url, announced_batches_in_order(learn_turn))
        wait_for(
            lambda: catalog_skill_id(client, backend_url, learned_name) is None,
            timeout_sec=60.0,
            what="the learned skill to disappear after the rollback",
        )


@pytest.mark.e2e
@pytest.mark.timeout(900)
@requires_live_model
def test_bare_learn_saves_a_skill_from_the_conversation_so_far() -> None:
    backend_url = isolated_backend_url()
    suffix = uuid.uuid4().hex[:8]
    marker = f"LEARN_CONVERSATION_MARKER_{suffix}"
    chat_id = f"learnbare{suffix}"

    with httpx.Client() as client:
        agent_id = create_agent(client, backend_url, suffix)
        create_chat(client, backend_url, chat_id, agent_id)
        known = set(catalog_skills(client, backend_url).values())

        told = stream_turn(client, backend_url, agent_id, chat_id, workflow_message(marker), security_preset=EXPLORE)
        assert not announced_batches(told), f"the plain turn saved a skill; stream: {stream_digest(told)}"

        learn_turn = stream_turn(client, backend_url, agent_id, chat_id, "/learn", security_preset=EXPLORE)
        batches = announced_batches(learn_turn)
        # The learned skills are undone even when an assertion below fails, so a red run leaves the catalog as it found it.
        try:
            assert not save_denials(learn_turn), f"the bare /learn turn's save was refused; stream: {stream_digest(learn_turn)}"
            assert batches, f"the bare /learn turn never saved a skill; stream: {stream_digest(learn_turn)}"
            learned = {name: skill_id for name, skill_id in catalog_skills(client, backend_url).items() if skill_id not in known}
            assert learned, f"the bare /learn turn announced {sorted(batches)} but no new skill reached the catalog"
            saved = {name: skill_md(client, backend_url, skill_id) for name, skill_id in learned.items()}
            assert any(marker in text for text in saved.values()), (
                f"no learned skill carries the step the user named in the conversation ({marker}); "
                f"saved: {[(name, text[:300]) for name, text in saved.items()]}"
            )
            print(f"E2E_BARE_LEARN_SAVED: skills={sorted(learned)} batches={sorted(batches)}", flush=True)
        finally:
            roll_back(client, backend_url, announced_batches_in_order(learn_turn))
        wait_for(
            lambda: not set(catalog_skills(client, backend_url).values()) & set(learned.values()),
            timeout_sec=60.0,
            what="the learned skills to disappear after the rollback",
        )


@pytest.mark.e2e
@pytest.mark.timeout(900)
@requires_live_model
def test_a_lone_slash_after_a_workflow_does_not_save_a_skill() -> None:
    backend_url = isolated_backend_url()
    suffix = uuid.uuid4().hex[:8]
    chat_id = f"learnslash{suffix}"

    with httpx.Client() as client:
        agent_id = create_agent(client, backend_url, suffix)
        create_chat(client, backend_url, chat_id, agent_id)
        known = set(catalog_skills(client, backend_url).values())

        told = stream_turn(
            client, backend_url, agent_id, chat_id, workflow_message(f"LEARN_SLASH_MARKER_{suffix}"), security_preset=EXPLORE
        )
        assert not announced_batches(told), f"the plain turn saved a skill; stream: {stream_digest(told)}"

        slash_turn = stream_turn(client, backend_url, agent_id, chat_id, "/", security_preset=EXPLORE)
        batches = announced_batches(slash_turn)
        try:
            assert any(event.get("type") == "message_end" for event in slash_turn), (
                f"the lone '/' turn never completed; stream: {stream_digest(slash_turn)}"
            )
            assert not batches, f"a lone '/' was run as /learn and saved a skill; stream: {stream_digest(slash_turn)}"
            saved = sorted(name for name, skill_id in catalog_skills(client, backend_url).items() if skill_id not in known)
            assert not saved, f"a lone '/' was run as /learn and saved {saved}"
            print("E2E_LONE_SLASH_SAVED_NOTHING", flush=True)
        finally:
            roll_back(client, backend_url, announced_batches_in_order(slash_turn))
