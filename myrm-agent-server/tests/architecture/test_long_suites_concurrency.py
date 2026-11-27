"""A push to main never cancels the run in progress of a suite that outlasts the gap between pushes.

[INPUT]
- .github/workflows/harness-test.yml: its concurrency block
- .github/workflows/server-unit-tests.yml: its concurrency block
- tests.support.desktop_release::WORKFLOWS (POS: the repository locations and workflow reads that the desktop contract
  tests and the gate guards share)

[OUTPUT]
- that each workflow cancels the run in progress for a pull request only, and that a pull request's runs form their
  own group, apart from the runs of main

[POS]
The harness suite takes about eighteen minutes and the server suite more than half an hour, while pushes to main
arrive closer together than that. A run that a later push cancels ends without a verdict, and the commit that broke
the suite is then named by no run. A push therefore waits for the run in progress (one runs, one waits, a newer push
replaces the waiting one), and only a pull request, whose later push supersedes its earlier one, cancels.
"""

from __future__ import annotations

from typing import cast

import pytest
import yaml

from tests.support.desktop_release import WORKFLOWS

_LONG_SUITES = ("harness-test.yml", "server-unit-tests.yml")
_CANCEL_ONLY_FOR_A_PULL_REQUEST = "${{ github.event_name == 'pull_request' }}"

_Concurrency = dict[str, str]


def _concurrency(workflow: str) -> _Concurrency:
    document = cast("dict[str, _Concurrency]", yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8")))
    return document["concurrency"]


@pytest.mark.parametrize("workflow", _LONG_SUITES)
def test_only_a_pull_request_cancels_the_run_in_progress(workflow: str) -> None:
    assert _concurrency(workflow)["cancel-in-progress"] == _CANCEL_ONLY_FOR_A_PULL_REQUEST


@pytest.mark.parametrize("workflow", _LONG_SUITES)
def test_a_pull_request_has_a_group_of_its_own(workflow: str) -> None:
    group = _concurrency(workflow)["group"]

    assert "github.event.pull_request.number" in group, f"{workflow}: a pull request's runs would share the group of main"
    assert "github.ref" in group, f"{workflow}: the runs of main would not be grouped by their ref"
