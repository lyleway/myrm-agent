"""The contract workflow runs every real-binary test there is and installs every agent the registry knows.

The real binaries are the contract of the delegation runtime, and a real-binary test that no job runs proves nothing:
it skips on every machine that has no CLI. These hold the workflow to what is on disk and in the registry, so a test
added without being wired in, an agent added without its binary, and an agent added without a real turn run against it
fail here, before a green run can pass for coverage it does not have.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from myrm_agent_harness.toolkits.acp.drivers import all_drivers

_HARNESS = Path(__file__).resolve().parents[3]
_WORKFLOWS = _HARNESS.parent / ".github" / "workflows"
_CONTRACT = _WORKFLOWS / "harness-cli-contract.yml"
_ACP_TESTS = Path(__file__).parent

pytestmark = pytest.mark.skipif(
    not _WORKFLOWS.is_dir(), reason="the harness is checked out without the workflows of its repository"
)

# For each agent of the registry, the real-binary test that runs a turn of it through the runtime built for it. An agent
# is admitted to the registry with a line here, which names a test that exists and that the workflow runs.
_REAL_TURNS = {
    "claude": "runtime/cli/test_real_claude_turn_end.py",
    "codex": "runtime/codex/test_real_codex_app_server.py",
    "gemini": "runtime/acp/test_real_acp_session_continuity.py",
    "qwen": "runtime/acp/test_real_acp_session_continuity.py",
}


def _jobs() -> dict[str, dict[str, object]]:
    jobs = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _commands(job: dict[str, object]) -> list[str]:
    steps = job["steps"]
    assert isinstance(steps, list)
    return [str(step["run"]) for step in steps if isinstance(step, dict) and "run" in step]


def _is_integration_module(path: Path) -> bool:
    """Whether the module marks all its tests ``integration`` (the ones that need a real binary)."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            return "pytest.mark.integration" in ast.unparse(node.value)
    return False


def _run_by_the_workflow() -> set[str]:
    """The test files the workflow's jobs name, as paths below the harness."""
    named: set[str] = set()
    for job in _jobs().values():
        for command in _commands(job):
            named.update(word for word in command.split() if word.startswith("tests/toolkits/acp/"))
    return named


def test_the_workflow_runs_every_real_binary_test_and_only_those() -> None:
    real = {path.relative_to(_HARNESS).as_posix() for path in _ACP_TESTS.rglob("*.py") if _is_integration_module(path)}
    named = _run_by_the_workflow()

    assert real - named == set(), "real-binary tests that no job of the workflow runs"
    assert named - real == set(), "files the workflow runs that are not real-binary tests"


def test_the_contract_job_installs_and_requires_the_binary_of_every_agent_of_the_registry() -> None:
    job = _jobs()["contract"]
    env = job["env"]
    assert isinstance(env, dict)
    required = set(str(env["MYRM_CONTRACT_REQUIRE_CLIS"]).split(","))
    installed = {word for command in _commands(job) if "npm install" in command for word in command.split()}

    for spec in all_drivers():
        assert spec.backend in required, f"{spec.backend} is not required: its tests would skip without its binary"
        assert spec.npm_package in installed, f"{spec.npm_package} is not installed by the contract job"


def test_every_agent_of_the_registry_has_a_real_turn_that_the_workflow_runs() -> None:
    assert set(_REAL_TURNS) == {spec.backend for spec in all_drivers()}

    named = _run_by_the_workflow()
    for backend, test in _REAL_TURNS.items():
        assert (_ACP_TESTS / test).is_file(), f"the real turn of {backend} is not a file: {test}"
        assert f"tests/toolkits/acp/{test}" in named, f"the workflow does not run the real turn of {backend}"
