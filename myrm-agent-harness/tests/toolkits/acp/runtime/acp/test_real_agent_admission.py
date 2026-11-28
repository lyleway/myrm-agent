"""An agent the host drives over ACP is not admitted until the real binary has been run against what it needs.

Every ACP agent in the registry is held to the same suites: a scripted model to answer its turns (permission round
trips, session continuity), and the record of what its ``initialize`` answered when the runtime was built on it. An
agent added to the registry without them fails here, so the ones that make the runtime work are never taken on trust.
"""

from __future__ import annotations

from myrm_agent_harness.toolkits.acp.drivers import all_drivers
from tests.toolkits.acp.acp_contract import ACP_ARGS, ADVERTISED_SESSION_CAPABILITIES

from ._real_agents import SCRIPTED_AGENTS

_ACP_AGENTS = {spec.backend for spec in all_drivers() if spec.transport == "acp"}


def test_every_agent_driven_over_acp_has_a_scripted_model_the_real_binary_runs_against() -> None:
    assert set(SCRIPTED_AGENTS) == _ACP_AGENTS


def test_every_agent_driven_over_acp_has_its_initialize_answer_on_record() -> None:
    assert set(ACP_ARGS) >= _ACP_AGENTS
    assert set(ADVERTISED_SESSION_CAPABILITIES) >= _ACP_AGENTS
