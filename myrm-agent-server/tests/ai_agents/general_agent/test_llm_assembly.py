"""The LLM assembly step hands every auxiliary caller one lite model that went through all three protections.

Only the model factory (and the org policy lookup that reads the sandbox config) is replaced; the context
downgrade, the lite fallback and the privacy routing run for real, so a bundle that drifts from the wrapper shows.

[INPUT]
- app/ai_agents/general_agent/factory/llms.py::initialize_agent_llms (POS: unit under test, executed for real)

[OUTPUT]
- Tests that the bundle's lite model and the wrapper's are one object, in each protection state.

[POS]
Guards the first assembly stage against a second, unprotected variable for the lite model.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from myrm_agent_harness.toolkits.llms.fallback import ManagedLLM
from myrm_agent_harness.toolkits.llms.routing import PrivacyRoutingModel

from app.ai_agents.agents import AgentFactory, GeneralAgentParams
from app.ai_agents.general_agent.agent import GeneralAgent
from app.ai_agents.general_agent.factory.llms import initialize_agent_llms
from app.core.types.business import ModelConfig

_WINDOWS = {"main-model": 128_000, "lite-small": 8_000, "lite-big": 128_000, "lite-backup": 128_000}


class _NamedModel(FakeListChatModel):
    """A fake chat model that carries the name and the context window the downgrade reads."""

    model: str = ""
    max_input_tokens: int = 128_000


@pytest.fixture(autouse=True)
def _model_factory_and_org_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def build(cfg: ModelConfig, api_keys: object = None) -> _NamedModel:
        return _NamedModel(responses=["ok"], model=cfg.model, max_input_tokens=_WINDOWS[cfg.model])

    monkeypatch.setattr("app.ai_agents.general_agent.llm_factory.llm_manager.get_llm_from_config", build)
    monkeypatch.setattr("app.services.org_model_policy.enforce.enforce_org_model_policy", AsyncMock())


def _wrapper(**params: object) -> GeneralAgent:
    fields: dict[str, object] = {
        "model_cfg": ModelConfig(model="main-model", api_key="k"),
        "query": "assemble the models",
        "chat_id": "llm-assembly-chat",
        "enable_browser": False,
        "prompt_mode": "full",
        **params,
    }
    return AgentFactory.create_general_agent(GeneralAgentParams.model_validate(fields))


def _cfg(model: str) -> ModelConfig:
    return ModelConfig(model=model, api_key="k")


async def test_a_lite_model_with_a_small_window_is_replaced_by_the_main_configuration_in_both_places() -> None:
    wrapper = _wrapper(lite_model_cfg=_cfg("lite-small"))

    bundle = await initialize_agent_llms(wrapper)

    assert bundle.lite_llm is wrapper._lite_llm
    assert bundle.lite_llm.model == "main-model"
    assert bundle.lite_llm is not bundle.llm


async def test_a_configured_lite_fallback_wraps_the_lite_model_in_both_places() -> None:
    wrapper = _wrapper(lite_model_cfg=_cfg("lite-big"), fallback_lite_model_cfg=_cfg("lite-backup"))

    bundle = await initialize_agent_llms(wrapper)

    assert isinstance(bundle.lite_llm, ManagedLLM)
    assert bundle.lite_llm is wrapper._lite_llm


async def test_privacy_routing_wraps_the_lite_model_and_leaves_the_main_model_alone() -> None:
    wrapper = _wrapper(
        lite_model_cfg=_cfg("lite-big"),
        privacy_routing_raw={"localModel": "ollama/llama3", "localBaseUrl": "http://127.0.0.1:11434/v1"},
    )

    bundle = await initialize_agent_llms(wrapper)

    assert isinstance(bundle.lite_llm, PrivacyRoutingModel)
    assert bundle.lite_llm is wrapper._lite_llm
    assert not isinstance(bundle.llm, PrivacyRoutingModel)


async def test_the_three_protections_apply_in_order_downgrade_then_fallback_then_privacy() -> None:
    wrapper = _wrapper(
        lite_model_cfg=_cfg("lite-small"),
        fallback_lite_model_cfg=_cfg("lite-backup"),
        privacy_routing_raw={"localModel": "ollama/llama3", "localBaseUrl": "http://127.0.0.1:11434/v1"},
    )

    bundle = await initialize_agent_llms(wrapper)

    assert isinstance(bundle.lite_llm, PrivacyRoutingModel)
    assert bundle.lite_llm is wrapper._lite_llm
    cloud = bundle.lite_llm.cloud_llm
    assert isinstance(cloud, ManagedLLM)
    assert cloud.model == "main-model"
