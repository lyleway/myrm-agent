"""The judge of the real-agent handshake goes red when an agent drops a capability the runtime negotiates.

The live handshake tests (``-m integration``, installed agents) are only worth their CI minutes if the judge cannot be
fooled, so these tests need no agent: they build ``initialize`` answers with the SDK's own schema and take capabilities
away one at a time.
"""

from __future__ import annotations

import pytest
from acp import PROTOCOL_VERSION
from acp.schema import (
    AgentCapabilities,
    AuthMethodAgent,
    InitializeResponse,
    McpCapabilities,
    PromptCapabilities,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionForkCapabilities,
    SessionListCapabilities,
    SessionResumeCapabilities,
)

from tests.toolkits.acp.acp_contract import ACP_ARGS, ADVERTISED_SESSION_CAPABILITIES, handshake_gaps


def _answer(
    *,
    protocol_version: int = PROTOCOL_VERSION,
    load_session: bool = True,
    image: bool = True,
    http: bool = True,
    auth: bool = True,
    session: SessionCapabilities | None = None,
) -> InitializeResponse:
    return InitializeResponse(
        protocol_version=protocol_version,
        agent_capabilities=AgentCapabilities(
            load_session=load_session,
            prompt_capabilities=PromptCapabilities(image=image),
            mcp_capabilities=McpCapabilities(http=http),
            session_capabilities=session,
        ),
        auth_methods=[AuthMethodAgent(id="login", name="Log in")] if auth else [],
    )


QWEN_SESSION = SessionCapabilities(list=SessionListCapabilities(), resume=SessionResumeCapabilities())


def test_every_known_agent_has_the_launch_arguments_and_the_capabilities_this_contract_judges() -> None:
    assert set(ACP_ARGS) == set(ADVERTISED_SESSION_CAPABILITIES)


def test_an_agent_that_advertises_what_it_did_is_taken() -> None:
    assert handshake_gaps("qwen", _answer(session=QWEN_SESSION), protocol_version=PROTOCOL_VERSION) == []


def test_an_agent_that_advertises_more_than_it_did_is_no_regression() -> None:
    richer = SessionCapabilities(
        list=SessionListCapabilities(),
        resume=SessionResumeCapabilities(),
        fork=SessionForkCapabilities(),
        close=SessionCloseCapabilities(),
    )

    assert handshake_gaps("qwen", _answer(session=richer), protocol_version=PROTOCOL_VERSION) == []


@pytest.mark.parametrize(
    ("answer", "gap"),
    [
        (_answer(session=QWEN_SESSION, load_session=False), "loadSession"),
        (_answer(session=QWEN_SESSION, image=False), "promptCapabilities.image"),
        (_answer(session=QWEN_SESSION, http=False), "mcpCapabilities.http"),
        (_answer(session=QWEN_SESSION, auth=False), "authMethods (a refused login could not be explained)"),
        (_answer(session=SessionCapabilities(list=SessionListCapabilities())), "sessionCapabilities.resume"),
        (_answer(session=None), "sessionCapabilities.list"),
        (_answer(session=QWEN_SESSION, protocol_version=PROTOCOL_VERSION + 1), "protocol version"),
    ],
)
def test_a_capability_the_runtime_negotiates_that_goes_missing_is_named(answer: InitializeResponse, gap: str) -> None:
    gaps = handshake_gaps("qwen", answer, protocol_version=PROTOCOL_VERSION)

    assert any(item.startswith(gap) for item in gaps), gaps


def test_an_agent_that_advertises_nothing_gets_every_gap_named() -> None:
    gaps = handshake_gaps(
        "qwen", InitializeResponse(protocol_version=PROTOCOL_VERSION), protocol_version=PROTOCOL_VERSION
    )

    assert [item.split(" ")[0] for item in gaps] == [
        "loadSession",
        "promptCapabilities.image",
        "mcpCapabilities.http",
        "authMethods",
        "sessionCapabilities.list",
        "sessionCapabilities.resume",
    ]


def test_an_answer_without_agent_capabilities_is_refused() -> None:
    answer = InitializeResponse.model_construct(protocol_version=PROTOCOL_VERSION, agent_capabilities=None)

    assert handshake_gaps("gemini", answer, protocol_version=PROTOCOL_VERSION) == ["no agent capabilities"]
