"""Texts shared by the runtimes: how an agent's message is set apart from the text before a tool call."""

from __future__ import annotations

import pytest

from myrm_agent_harness.toolkits.acp.runtime.event_text import paragraph_break


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("Let me look.", "Found it.", "\n\n"),
        ("Let me look.\n", "Found it.", "\n"),
        ("Let me look.", "\nFound it.", "\n"),
        ("Let me look.\n\n", "Found it.", ""),
        ("Let me look.\n", "\nFound it.", ""),
        ("Let me look.", "\n\nFound it.", ""),
        ("Let me look.\n\n\n", "\nFound it.", ""),
        ("", "Found it.", ""),
    ],
    ids=[
        "joined",
        "newline-before",
        "newline-after",
        "blank-line-before",
        "one-on-each-side",
        "blank-line-after",
        "more-than-enough",
        "first-message",
    ],
)
def test_a_message_is_set_apart_from_the_text_before_it_by_exactly_one_blank_line(
    before: str, after: str, expected: str
) -> None:
    assert paragraph_break(before, after) == expected
