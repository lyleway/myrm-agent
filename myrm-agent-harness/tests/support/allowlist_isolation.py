"""Keep the standing approvals a test adds away from the next test (a pytest plugin).

[INPUT]
- agent.security.approval_flow::_allowlist, Allowlist (POS: the process-wide store of the user's "allow always" entries, which lift an ASK of the approval engine to an AUTO-APPROVE)

[OUTPUT]
- isolated_allowlist: context manager; the allowlist is empty on the way in and again on the way out
- _isolate_approval_allowlist: autouse fixture; every test runs inside ``isolated_allowlist``

[POS]
Registered from ``tests/conftest.py`` (``pytest_plugins``). The allowlist belongs to the process, not to a test: a
standing approval of the bash tool that one test adds is still there when the next test of the worker evaluates a command
as the same user, and the ASK that test pins comes back as an AUTO-APPROVE. Which module ran before which depends on how
xdist hands out the modules, so without this the same code passes in one run and fails in the next. A process that never
imported the module holds no allowlist, so nothing is imported here.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

_APPROVAL_FLOW = "myrm_agent_harness.agent.security.approval_flow"


def _empty_the_allowlist() -> None:
    if _APPROVAL_FLOW not in sys.modules:
        return
    from myrm_agent_harness.agent.security import approval_flow

    approval_flow._allowlist = approval_flow.Allowlist()


@contextmanager
def isolated_allowlist() -> Iterator[None]:
    _empty_the_allowlist()
    try:
        yield
    finally:
        _empty_the_allowlist()


@pytest.fixture(autouse=True)
def _isolate_approval_allowlist() -> Iterator[None]:
    with isolated_allowlist():
        yield
