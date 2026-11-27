"""A workflow that a pull request triggers also runs when the same files are pushed to main.

[INPUT]
- .github/workflows/*.yml: the filters under each workflow's push and pull_request triggers
- tests.support.desktop_release::WORKFLOWS (POS: the repository locations and workflow reads that the desktop contract
  tests and the gate guards share)

[OUTPUT]
- that every workflow with a pull_request trigger also has a push trigger on main, and that the two triggers carry
  the same path filter

[POS]
The maintainers push to main directly and a pull request is the exception, so a gate that only a pull request
triggers measures nothing on the code that ships: its last verdict is whatever the last pull request left, however long
ago, and a change that breaks it lands without a red mark. A workflow therefore runs on the push wherever it runs on a
pull request, over the same files (a narrower filter on the push would only make the gap smaller). A schedule or a
manual trigger comes on top of the push trigger, never in its place.
"""

from __future__ import annotations

from typing import cast

import pytest
import yaml

from tests.support.desktop_release import WORKFLOWS

_PATH_FILTERS = frozenset({"paths", "paths-ignore"})

# `on:` -> {event: {"branches": [...], "paths": [...]} | None}; YAML 1.1 reads the key `on` as the boolean True, and an
# event written without options (`pull_request:`) as None.
_Filter = dict[str, list[str]]
_Triggers = dict[str, _Filter | None]
_WorkflowDocument = dict[bool, _Triggers]


def _triggers(workflow: str) -> _Triggers:
    document = cast("_WorkflowDocument", yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8")))
    return document[True]


def _scope(triggers: _Triggers, event: str) -> _Filter:
    return {key: value for key, value in (triggers.get(event) or {}).items() if key in _PATH_FILTERS}


# GitHub runs a workflow file with either suffix; a `.yaml` one must not slip past the check.
_PULL_REQUEST_GATES = sorted(
    path.name for path in WORKFLOWS.iterdir() if path.suffix in {".yml", ".yaml"} and "pull_request" in _triggers(path.name)
)


def test_the_pull_request_gates_are_found() -> None:
    # A scan that finds nothing would pass every case below by generating none of them.
    assert "server-unit-tests.yml" in _PULL_REQUEST_GATES


@pytest.mark.parametrize("workflow", _PULL_REQUEST_GATES)
def test_a_pull_request_gate_also_runs_on_push_to_main(workflow: str) -> None:
    triggers = _triggers(workflow)

    assert "push" in triggers, f"{workflow}: a change pushed to main skips what a pull request would run"
    assert (triggers["push"] or {}).get("branches") == ["main"], f"{workflow}: the push trigger must name main"


@pytest.mark.parametrize("workflow", _PULL_REQUEST_GATES)
def test_push_and_pull_request_cover_the_same_files(workflow: str) -> None:
    triggers = _triggers(workflow)

    assert _scope(triggers, "push") == _scope(triggers, "pull_request"), f"{workflow}: the two triggers differ in scope"
