"""The integration write pattern registry of the shell command analyzer.

The harness ships no vendor rule: the business layer registers its table at startup, and the table the product ships is
tested with it (``myrm-agent-server/tests/core/security/test_integration_write_patterns.py``). These tests register a
pattern of their own, so they pin the seam and how a registered pattern is read against every command a line runs.
"""

from __future__ import annotations

import pytest

from myrm_agent_harness.toolkits.code_execution.security import shell_command_analyzer
from myrm_agent_harness.toolkits.code_execution.security.shell_command_analyzer import (
    has_escalate_threat,
    is_integration_mutation_command,
    register_integration_write_patterns,
)

_ACME_PATTERNS = ((r"\bacme-cli\s+send\b", "acme send"),)
_ACME_SEND = "acme-cli send --to a@b.com --subject Hi"
_ACME_LIST = "acme-cli list --today"


@pytest.fixture(autouse=True)
def _empty_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry is process-global: each test starts from an empty one and the original comes back afterwards."""
    monkeypatch.setattr(shell_command_analyzer, "_EXTRA_INTEGRATION_WRITE_PATTERNS", [])


class TestIntegrationWriteRegistry:
    def test_no_write_is_found_until_a_pattern_is_registered(self) -> None:
        assert is_integration_mutation_command(_ACME_SEND) is False
        assert has_escalate_threat(_ACME_SEND) is None

    def test_a_registered_pattern_marks_the_write_and_escalates_it(self) -> None:
        register_integration_write_patterns(_ACME_PATTERNS)

        assert is_integration_mutation_command(_ACME_SEND) is True
        threat = has_escalate_threat(_ACME_SEND)
        assert threat is not None
        assert threat.category == "integration_mutation"
        assert is_integration_mutation_command(_ACME_LIST) is False

    def test_registering_the_same_pattern_twice_keeps_one_rule(self) -> None:
        register_integration_write_patterns(_ACME_PATTERNS)
        register_integration_write_patterns(_ACME_PATTERNS)

        assert len(shell_command_analyzer._EXTRA_INTEGRATION_WRITE_PATTERNS) == 1

    @pytest.mark.parametrize(
        "template",
        [
            "echo start;{c}",
            "{c}&&echo done",
            "sudo {c}",
            "for i in 1; do {c}; done",
            "bash -c '{c}'",
        ],
    )
    def test_the_write_is_found_in_a_command_the_line_runs(self, template: str) -> None:
        register_integration_write_patterns(_ACME_PATTERNS)

        assert is_integration_mutation_command(template.format(c=_ACME_SEND)) is True

    def test_text_that_only_mentions_the_write_is_no_mutation(self) -> None:
        register_integration_write_patterns(_ACME_PATTERNS)

        assert is_integration_mutation_command(f"echo '{_ACME_SEND}'") is False
