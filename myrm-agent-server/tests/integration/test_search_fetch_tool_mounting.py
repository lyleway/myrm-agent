"""Integration: the mounted web tools.

web_fetch_tool is the same read-in-full tool whatever retrieval services are configured, and each choice the user or
the deployment makes reaches the tool it is meant for.
"""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import BaseTool
from myrm_agent_harness.api import LLMConfig
from myrm_agent_harness.toolkits.retriever.sufficiency import SufficiencyConfig
from myrm_agent_harness.toolkits.web_search.engine import SearchServiceConfig

from app.ai_agents.general_agent.tool_setup import ToolSetupMixin
from app.core.types import ModelConfig


def _mixin(**overrides: object) -> ToolSetupMixin:
    mixin = ToolSetupMixin.__new__(ToolSetupMixin)
    defaults: dict[str, object] = {
        "enable_web_fetch": True,
        "enable_web_search": True,
        "search_service_cfg": SearchServiceConfig(search_service="tavily", api_key="test-key"),
        "search_depth": "standard",
        "fetch_raw_webpage": False,
        "enable_advanced_retrieval": False,
        "reranker_config": None,
        "embedding_config": None,
        "channel_name": "web_chat",
        "skill_ids": [],
        "model_cfg": ModelConfig(model="test/model", api_key="test-key"),
        "image_generation_params": None,
        "video_generation_params": None,
        "tts_params": None,
    }
    for key, value in {**defaults, **overrides}.items():
        setattr(mixin, key, value)
    return mixin


def _mounted(mixin: ToolSetupMixin) -> dict[str, BaseTool]:
    tools: list[object] = []
    mixin._setup_search_and_basic_tools(tools)
    return {tool.name: tool for tool in tools if isinstance(tool, BaseTool)}


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"enable_advanced_retrieval": True}, id="advanced-on-without-configs"),
        pytest.param(
            {"enable_advanced_retrieval": True, "reranker_config": MagicMock(), "embedding_config": MagicMock()},
            id="advanced-on-with-both-configs",
        ),
        pytest.param(
            {"enable_advanced_retrieval": False, "reranker_config": MagicMock(), "embedding_config": MagicMock()},
            id="configs-present-but-advanced-off",
        ),
    ],
)
def test_the_fetch_tool_does_not_depend_on_the_retrieval_configuration(overrides: dict[str, object]) -> None:
    baseline = _mounted(_mixin())["web_fetch_tool"]
    tools = _mounted(_mixin(**overrides))

    fetch = tools["web_fetch_tool"]
    assert set(fetch.get_input_jsonschema()["properties"]) == {"urls", "reason"}
    assert fetch.description == baseline.description
    assert "fetch_and_extract" not in tools["web_search_tool"].description


def test_search_never_mentions_fetch_when_web_fetch_is_not_mounted() -> None:
    tools = _mounted(
        _mixin(
            enable_web_fetch=False,
            enable_advanced_retrieval=True,
            reranker_config=MagicMock(),
            embedding_config=MagicMock(),
        )
    )

    assert "web_fetch_tool" not in tools
    assert "fetch_and_extract" not in tools["web_search_tool"].description
    assert "web_fetch_tool" not in tools["web_search_tool"].description


def _created_with(mixin: ToolSetupMixin, *, local: bool = False) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """The keyword arguments the web fetch tool and the web search tool are created with."""
    with (
        patch("myrm_agent_harness.toolkits.create_web_fetch_tool") as fetch,
        patch("myrm_agent_harness.toolkits.create_web_search_tool") as search,
        patch("app.config.deploy_mode.is_local_mode", return_value=local),
        patch.object(ToolSetupMixin, "_setup_image_generation_tools"),
        patch.object(ToolSetupMixin, "_setup_video_generation_tools"),
        patch.object(ToolSetupMixin, "_setup_tts_tools"),
    ):
        mixin._setup_search_and_basic_tools([])
    return fetch.call_args.kwargs, search.call_args.kwargs


@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize("raw", [True, False])
def test_the_fetch_tool_follows_the_deployment_and_the_raw_page_setting(local: bool, raw: bool) -> None:
    fetch, _ = _created_with(_mixin(fetch_raw_webpage=raw), local=local)

    assert fetch["allow_private_networks"] is local
    assert fetch["use_raw_markdown"] is raw


def test_the_evaluation_blocklists_reach_the_tools_they_filter() -> None:
    fetch, search = _created_with(
        _mixin(benchmark_blocked_hostnames=("answers.example",), benchmark_blocked_terms=("leaked question",))
    )

    assert fetch["blocked_hostnames"] == ("answers.example",)
    assert search["blocked_hostnames"] == ("answers.example",)
    assert search["blocked_terms"] == ("leaked question",)

    fetch, search = _created_with(_mixin())
    assert (fetch["blocked_hostnames"], search["blocked_hostnames"], search["blocked_terms"]) == (None, None, None)


def test_the_sufficiency_check_is_wired_to_deep_search_only() -> None:
    fetch, search = _created_with(_mixin(search_depth="deep"))

    config, llm = search["sufficiency_config"], search["sufficiency_llm_config"]
    assert isinstance(config, SufficiencyConfig) and config.enabled
    assert isinstance(llm, LLMConfig) and llm.model == "test/model"
    assert not {"sufficiency_config", "sufficiency_llm_config"} & fetch.keys()

    _, search = _created_with(_mixin(search_depth="standard"))
    assert (search["sufficiency_config"], search["sufficiency_llm_config"]) == (None, None)
