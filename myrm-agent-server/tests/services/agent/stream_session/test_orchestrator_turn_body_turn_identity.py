"""The turn body stamps the memo turn identity before the marker serialises params.

The identity is written after ``params`` is built; writing it earlier would raise ``UnboundLocalError`` on every
persisted turn (``orchestrator_turn_body.py``). This test drives the real body with the surrounding pipeline
stubbed so the assignment point is what is exercised. It also pins what the body hands the pre-reply compaction: the
turn's own stream buffer, chat, message, agent and engine params.
"""

from __future__ import annotations

import pytest

from tests.services.agent.stream_session.turn_body_support import (
    AGENT_ID,
    CHAT_ID,
    MESSAGE_ID,
    PERSISTED_MESSAGE_ID,
    run_turn_body,
    stub_turn_body,
)


@pytest.mark.asyncio
async def test_turn_identity_is_stamped_on_the_built_params(monkeypatch: pytest.MonkeyPatch) -> None:
    run = stub_turn_body(monkeypatch)

    await run_turn_body(run)

    run.pre_reply_compact.assert_awaited_once_with(
        run.buffer, chat_id=CHAT_ID, message_id=MESSAGE_ID, agent_id=AGENT_ID, request_engine_params=None
    )
    (write,) = run.marker_writes
    assert write.params is run.params
    assert write.turn_identity == "msg-persisted"
    assert write.user_message_id == PERSISTED_MESSAGE_ID
    assert run.params.turn_identity == "msg-persisted"
    assert len(run.pumped) == 1
    run.release_unfinished.assert_awaited_once_with(run.pumped[0])
