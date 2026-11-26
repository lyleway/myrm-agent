"""``/learn`` detection leaves every message that is not a ``/learn`` request alone.

[INPUT]
- app.core.channel_bridge.learn_handler (POS: /learn slash parsing and the skill-authoring turn rewrite)

[OUTPUT]
- A bare slash, another command, a ``/learn`` that is not a plain string or block list, and block lists without text
  are not ``/learn`` requests: they are neither parsed nor rewritten and never count as an authoring turn; the
  command name is matched case-insensitively.

[POS]
tests/core/channel_bridge/test_learn_query_shapes.py
"""

from __future__ import annotations

import pytest

from app.core.channel_bridge.learn_handler import (
    learn_authoring_prompt_text,
    parse_learn_slash_args,
    rewrite_learn_query_if_needed,
)

NOT_LEARN_REQUESTS: list[object] = [
    pytest.param("/", id="bare-slash"),
    pytest.param("  /  ", id="padded-bare-slash"),
    pytest.param("/compact now", id="other-command"),
    pytest.param("/ learn the workflow", id="space-after-slash"),
    pytest.param(None, id="none"),
    pytest.param(42, id="number"),
    pytest.param({"type": "text", "text": "/learn"}, id="single-block-not-in-a-list"),
    pytest.param([], id="no-blocks"),
    pytest.param([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}], id="image-only"),
    pytest.param([{"type": "text", "text": "   "}], id="blank-text-block"),
]


@pytest.mark.parametrize("query", NOT_LEARN_REQUESTS)
def test_a_message_that_is_not_a_learn_request_is_not_parsed(query: object) -> None:
    assert parse_learn_slash_args(query) is None


@pytest.mark.parametrize("query", NOT_LEARN_REQUESTS)
def test_a_message_that_is_not_a_learn_request_is_not_rewritten(query: object) -> None:
    assert rewrite_learn_query_if_needed(query) is query


@pytest.mark.parametrize("query", NOT_LEARN_REQUESTS)
def test_a_message_that_is_not_a_learn_request_is_not_an_authoring_turn(query: object) -> None:
    assert learn_authoring_prompt_text(query) is None


def test_the_command_name_is_matched_case_insensitively() -> None:
    assert parse_learn_slash_args("/LEARN  https://example.com ") == "https://example.com"
