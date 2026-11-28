"""The Fast Lane streams a bare chat model: the messages it sends, the plain call it makes, and the text it streams.

The lane has no tools and no graph; all it does is the call it makes to the model and what it makes of the chunks that
come back. The model is a scripted stand-in and everything else is the real lane.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from myrm_agent_harness.api import AgentEventType
from myrm_agent_harness.toolkits.llms import llm_manager

from app.ai_agents import GeneralAgentParams
from app.services.agent.stream_session.stream_lane_factory import create_fast_lane_stream


class _PlainModel:
    """A model that streams scripted chunks and records the call it received."""

    model_name = "fast-model"

    def __init__(self, *chunks: SimpleNamespace) -> None:
        self._chunks = chunks
        self.messages: list[BaseMessage] = []
        self.stream_kwargs: dict[str, object] = {}

    async def astream(self, messages: list[BaseMessage], **kwargs: object) -> AsyncIterator[SimpleNamespace]:
        self.messages = list(messages)
        self.stream_kwargs = kwargs
        for chunk in self._chunks:
            yield chunk


class _ChatModel(_PlainModel):
    """A model that can bind tools, as every chat model the lane meets can."""

    def bind_tools(self, *_tools: object) -> None:
        raise NotImplementedError


def _text(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content)


async def _run_lane(
    model: _PlainModel, *, history: list[BaseMessage] | None = None, query: str = "hello"
) -> list[dict[str, object]]:
    params = cast(
        GeneralAgentParams,
        SimpleNamespace(
            model_cfg=object(),
            user_instructions="Answer in one line.",
            chat_history=history,
            query=query,
            channel_name="web_chat",
            message_id="msg-fast",
        ),
    )
    with (
        patch.object(llm_manager, "get_llm_from_config", new=AsyncMock(return_value=model)),
        patch("app.core.utils.chat_utils.convert_chat_history", new=AsyncMock(return_value=history)),
    ):
        return [event async for event in create_fast_lane_stream(params, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("model_class", [_PlainModel, _ChatModel], ids=["plain-model", "model-that-binds-tools"])
async def test_the_model_is_streamed_with_a_plain_call_whatever_it_can_do(model_class: type[_PlainModel]) -> None:
    # The adapter asks the provider for usage by itself and forwards every other option into the provider request.
    model = model_class(_text("hi"))

    await _run_lane(model)

    assert model.stream_kwargs == {}


@pytest.mark.asyncio
async def test_the_model_sees_the_system_prompt_then_the_history_then_the_question() -> None:
    model = _PlainModel(_text("hi"))
    history: list[BaseMessage] = [HumanMessage(content="earlier question"), AIMessage(content="earlier answer")]

    await _run_lane(model, history=history, query="new question")

    assert isinstance(model.messages[0], SystemMessage)
    assert "Answer in one line." in str(model.messages[0].content)
    assert [(type(m), m.content) for m in model.messages[1:]] == [
        (HumanMessage, "earlier question"),
        (AIMessage, "earlier answer"),
        (HumanMessage, "new question"),
    ]


@pytest.mark.asyncio
async def test_the_text_of_each_chunk_is_streamed_and_the_run_ends_with_message_end() -> None:
    events = await _run_lane(_PlainModel(_text("he"), _text("llo")))

    assert [(e["type"], e.get("data")) for e in events] == [
        (AgentEventType.STATUS.value, {"status": "fast_lane_active"}),
        (AgentEventType.MESSAGE.value, "he"),
        (AgentEventType.MESSAGE.value, "llo"),
        (AgentEventType.MESSAGE_END.value, None),
    ]
