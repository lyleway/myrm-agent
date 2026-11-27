"""The Fast Lane streams a bare chat model: the messages it sends, how it asks for usage, and the usage it reports.

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
    """A modern chat model: the lane takes one that can bind tools to be one that reports usage while streaming."""

    def bind_tools(self, *_tools: object) -> None:
        raise NotImplementedError


def _text(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content, usage_metadata=None)


def _usage(**fields: object) -> SimpleNamespace:
    return SimpleNamespace(content="", usage_metadata=fields)


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


async def _token_usage(model: _PlainModel) -> dict[str, object]:
    events = await _run_lane(model)
    (event,) = [e for e in events if e["type"] == AgentEventType.TOKEN_USAGE.value]
    return cast(dict[str, object], event["data"])


@pytest.mark.asyncio
async def test_a_chat_model_is_asked_to_report_usage_while_it_streams() -> None:
    model = _ChatModel(_text("hi"))

    await _run_lane(model)

    assert model.stream_kwargs == {"stream_usage": True}


@pytest.mark.asyncio
async def test_a_model_that_cannot_bind_tools_is_streamed_without_the_usage_option() -> None:
    model = _PlainModel(_text("hi"))

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


@pytest.mark.asyncio
async def test_the_cached_tokens_come_from_the_prompt_token_details() -> None:
    data = await _token_usage(
        _PlainModel(
            _usage(
                input_tokens=2000,
                output_tokens=10,
                total_tokens=2010,
                prompt_tokens_details={"cached_tokens": 1536, "cache_creation_input_tokens": 64},
            )
        )
    )

    assert (data["cached_tokens"], data["cache_write_tokens"], data["cache_hit_rate"]) == (1536, 64, 0.768)


@pytest.mark.asyncio
async def test_a_top_level_cached_count_is_used_when_the_details_carry_none() -> None:
    data = await _token_usage(_PlainModel(_usage(input_tokens=1000, output_tokens=5, total_tokens=1005, cached_tokens=500)))

    assert data["cached_tokens"] == 500


@pytest.mark.asyncio
async def test_a_top_level_cached_count_that_is_not_an_integer_counts_as_none() -> None:
    data = await _token_usage(_PlainModel(_usage(input_tokens=1000, output_tokens=5, total_tokens=1005, cached_tokens="500")))

    assert (data["cached_tokens"], data["cache_hit_rate"]) == (0, 0.0)
