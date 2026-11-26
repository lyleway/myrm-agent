"""Which model the context pipeline speaks as, and which one does its auxiliary work.

The processors read the identity of the model that answers the request (cache markers, thinking-block handling)
and take summaries from the auxiliary model; the two are separate when the host passes both.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from myrm_agent_harness.agent.context_management.pipeline import ProcessorContext
from myrm_agent_harness.agent.context_management.pipeline.processors import ExplicitCacheProcessor
from myrm_agent_harness.agent.middlewares.context_pipeline.context_pipeline_middleware import (
    create_context_pipeline_middleware,
)

_CLAUDE = ("claude-sonnet-4-5", "https://api.anthropic.com")
_DEEPSEEK = ("deepseek-chat", "https://api.deepseek.com")


def _model(identity: tuple[str, str]) -> MagicMock:
    llm = MagicMock()
    llm.model, llm.api_base = identity
    return llm


async def _context_the_processors_see(llm: MagicMock, summarizer_llm: MagicMock | None) -> ProcessorContext:
    pipeline = MagicMock()
    pipeline.process = AsyncMock(
        return_value=ProcessorContext(messages=[HumanMessage(content="hi")], user_query="", chat_id="chat-1")
    )
    request = ModelRequest(
        model=llm,
        messages=[HumanMessage(content="hi")],
        runtime=Runtime(context={"chat_id": "chat-1", "max_context_tokens": 128_000}),
    )
    middleware = create_context_pipeline_middleware(llm=llm, summarizer_llm=summarizer_llm, pipeline=pipeline)
    await middleware.awrap_model_call(
        cast("ModelRequest[None]", request), AsyncMock(return_value=ModelResponse(result=[]))
    )
    return pipeline.process.await_args.args[0]


async def test_the_processors_take_the_identity_from_the_main_model_and_summaries_from_the_auxiliary_one() -> None:
    main, auxiliary = _model(_CLAUDE), _model(_DEEPSEEK)

    seen = await _context_the_processors_see(main, auxiliary)

    assert seen.llm is main
    assert seen.metadata["model_name"] == "claude-sonnet-4-5"
    assert seen.metadata["base_url"] == "https://api.anthropic.com"
    assert seen.summarizer_llm is auxiliary


async def test_without_an_auxiliary_model_the_main_model_summarizes() -> None:
    main = _model(_CLAUDE)

    seen = await _context_the_processors_see(main, None)

    assert seen.summarizer_llm is main


async def test_explicit_cache_markers_follow_the_main_model_not_the_auxiliary_one() -> None:
    markers_on = await _context_the_processors_see(_model(_CLAUDE), _model(_DEEPSEEK))
    markers_off = await _context_the_processors_see(_model(_DEEPSEEK), _model(_CLAUDE))

    assert await ExplicitCacheProcessor().should_process(markers_on) is True
    assert await ExplicitCacheProcessor().should_process(markers_off) is False
