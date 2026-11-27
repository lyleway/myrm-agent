"""A test starts without the standing approvals the test before it added.

[INPUT]
- agent.security.approval_flow::get_allowlist, AllowlistEntry, DEFAULT_USER_ID (POS: the process-wide allowlist the isolation fixture empties)
- agent.middlewares.approval.batch_processor::evaluate_tool_batch (POS: the production entry that reads the allowlist)
- tests.support.allowlist_isolation (POS: the plugin under test)

[OUTPUT]
- TestAllowlistBetweenTests: one chain, three tests, each of which must meet an empty allowlist
- TestIsolatedAllowlist: the block empties the allowlist on the way in and on the way out
- test_the_plugin_is_registered_in_every_run: the fixture is in the closure of every test

[POS]
Pins ``_isolate_approval_allowlist`` through the behaviour it protects: a command the approval engine asks about
must not reach AUTO-APPROVE because an earlier test of the worker added a standing approval of the bash tool.
"""

from __future__ import annotations

import pytest

from myrm_agent_harness.agent.middlewares.approval.batch_processor import evaluate_tool_batch
from myrm_agent_harness.agent.security.approval_flow import DEFAULT_USER_ID, AllowlistEntry, get_allowlist
from myrm_agent_harness.agent.security.channel_presets import build_channel_security_config
from tests.support.allowlist_isolation import isolated_allowlist

_BASH = AllowlistEntry(permission="code_interpreter", tool_name="bash_code_execute_tool")


def _bash_is_approved_for_good() -> bool:
    return get_allowlist().check(DEFAULT_USER_ID, _BASH.permission, _BASH.tool_name)


async def _reaches_auto_approve(command: str) -> bool:
    config = build_channel_security_config("web", local_mode=True)
    calls = [{"name": "bash_code_execute_tool", "args": {"command": command}, "id": "c1"}]
    approved, _denied, _pending = await evaluate_tool_batch(
        calls, config, False, "/tmp", "s", {0: "h"}, None, (), None, is_interactive=True
    )
    return bool(approved)


class TestAllowlistBetweenTests:
    """The three tests are one chain and only mean something in file order, which pytest keeps: the first adds the
    standing approval, the two after it must not meet it."""

    async def test_a_test_adds_a_standing_approval_of_the_bash_tool(self) -> None:
        await get_allowlist().add(DEFAULT_USER_ID, _BASH)

        assert _bash_is_approved_for_good()
        assert await _reaches_auto_approve("kubectl delete ns prod")

    async def test_an_async_test_after_it_asks_about_the_same_command(self) -> None:
        assert not _bash_is_approved_for_good()
        assert not await _reaches_auto_approve("kubectl delete ns prod")

    def test_a_sync_test_after_that_starts_empty_too(self) -> None:
        assert not _bash_is_approved_for_good()


class TestIsolatedAllowlist:
    async def test_entries_added_before_the_block_are_gone_inside_it(self) -> None:
        await get_allowlist().add(DEFAULT_USER_ID, _BASH)

        with isolated_allowlist():
            assert not _bash_is_approved_for_good()

    async def test_entries_added_inside_the_block_are_gone_after_it(self) -> None:
        with isolated_allowlist():
            await get_allowlist().add(DEFAULT_USER_ID, _BASH)
            assert _bash_is_approved_for_good()

        assert not _bash_is_approved_for_good()

    async def test_the_block_empties_the_allowlist_when_its_body_raises(self) -> None:
        with pytest.raises(RuntimeError), isolated_allowlist():
            await get_allowlist().add(DEFAULT_USER_ID, _BASH)
            raise RuntimeError("the body failed")

        assert not _bash_is_approved_for_good()


def test_the_plugin_is_registered_in_every_run(request: pytest.FixtureRequest) -> None:
    assert "_isolate_approval_allowlist" in request.fixturenames
