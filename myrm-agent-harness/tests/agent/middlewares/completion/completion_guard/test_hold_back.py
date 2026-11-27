"""What a held-back answer leaves in the thread and where the instruction follows it."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import myrm_agent_harness.agent.middlewares.completion.completion_guard.state as _cg_state
from myrm_agent_harness.agent.middlewares.completion.completion_guard import (
    CompletionGuard,
    reset_completion_guard,
)
from tests.support.completion_guard_harness import (
    LOOP_GUARD_PATCH,
    make_state,
    pending,
)


class TestHoldBack:
    """A held-back draft stays in the conversation and the instruction follows it."""

    def setup_method(self) -> None:
        self.guard = CompletionGuard()
        reset_completion_guard()

    @pytest.mark.asyncio
    async def test_a_held_back_text_draft_stays_and_the_instruction_follows_it(self) -> None:
        state = make_state(
            [
                HumanMessage(content="今天最新的 AI 新闻是什么？"),
                AIMessage(content="根据我所知，近期 AI 领域的主要进展包括多模态模型。"),
            ]
        )
        with patch(LOOP_GUARD_PATCH) as mock_guard:
            mock_guard.return_value.recorded_calls.return_value = []
            result = await self.guard.aafter_model(state, None)

        assert pending(result).anchor_id == state["messages"][1].id
        assert "messages" not in result

    @pytest.mark.asyncio
    async def test_an_answer_that_cannot_be_addressed_is_let_through(self) -> None:
        """A message without an id cannot anchor the instruction, so nothing is held back for it."""
        state = {
            "messages": [
                HumanMessage(content="今天最新的 AI 新闻是什么？"),
                AIMessage(content="根据我所知，近期 AI 领域的主要进展包括多模态模型。"),
            ]
        }
        with patch(LOOP_GUARD_PATCH) as mock_guard:
            mock_guard.return_value.recorded_calls.return_value = []
            result = await self.guard.aafter_model(state, None)

        assert result is None
        assert _cg_state.current_run_state().pending_directive is None
