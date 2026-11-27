"""The permission engine judges a file name with whitespace at an end as the file the tools open.

The tools open a path exactly as the model sent it, so a link named ``"a "`` that points at ``.env`` reads a credential
file. The verdict has to cover the file the tool returns whatever whitespace surrounds the name, and it must not
change for a stray space around an ordinary name. The real read and search tools are the oracle: each test asks the
real engine about the very call whose result it has just read.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableConfig

from myrm_agent_harness.agent.meta_tools.file_ops.file_read_tool import create_file_read_tool
from myrm_agent_harness.agent.meta_tools.file_search.grep_tool import create_grep_tool
from myrm_agent_harness.agent.security.engine import evaluate_tool_call, merge
from myrm_agent_harness.agent.security.types import (
    DEFAULT_RULESET,
    PermissionAction,
    PermissionRule,
    SecurityConfig,
)
from myrm_agent_harness.core.context_vars import workspace_root_var
from myrm_agent_harness.toolkits.code_execution import ExecutionConfig
from myrm_agent_harness.toolkits.code_execution.executors.base import reset_executor, set_executor
from myrm_agent_harness.toolkits.code_execution.executors.local import LocalExecutor
from myrm_agent_harness.toolkits.code_execution.utils.workspace_path import WorkspacePathResolver
from myrm_agent_harness.utils.errors import ToolError

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")

CREDENTIAL = "CANARY_ENV"
PLAN = "PRIVATE_PLAN"

# Every character ``str.strip`` removes: each is a spelling the verdict used to read as a different file.
WHITESPACE = tuple(char for char in map(chr, range(sys.maxunicode + 1)) if char.isspace())

# A name that ends, starts or both starts and ends with whitespace, and one made of nothing else.
NAME_SHAPES = ("{ws}a", "a{ws}", "{ws}a{ws}", "{ws}")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "ws"
    (root / "private").mkdir(parents=True)
    (root / ".env").write_text(f"{CREDENTIAL}=1\n")
    (root / "private" / "plan.md").write_text(f"{PLAN}\n")
    monkeypatch.setenv("WORKSPACE_ROOT", str(root))
    monkeypatch.setattr(WorkspacePathResolver, "_cached_workspace_root", None)
    executor_token = set_executor(LocalExecutor(ExecutionConfig(), workspace_path=str(root)))
    root_token = workspace_root_var.set(str(root))
    yield root
    workspace_root_var.reset(root_token)
    reset_executor(executor_token)


def _link_every_whitespace_name(root: Path, target: str) -> list[str]:
    """Create a link to ``target`` under every name the corpus makes; return the names the file system accepted."""
    created: list[str] = []
    for ws in WHITESPACE:
        for shape in NAME_SHAPES:
            name = shape.format(ws=ws)
            try:
                (root / name).symlink_to(root / target)
            except OSError:
                continue
            created.append(name)
    return created


async def _read(name: str) -> str:
    try:
        return str(await create_file_read_tool().ainvoke({"paths": [name]}, config=RunnableConfig()))
    except ToolError as failure:
        return failure.format_for_llm()


async def _search(name: str) -> str:
    try:
        return str(await create_grep_tool().ainvoke({"pattern": CREDENTIAL, "path": name}, config=RunnableConfig()))
    except ToolError as failure:
        return failure.format_for_llm()


def _verdict(
    args: dict[str, object], root: Path, config: SecurityConfig | None = None, tool_name: str = "file_read_tool"
) -> PermissionAction:
    return evaluate_tool_call(
        "file_read", args, config or SecurityConfig(), workspace_root=str(root), tool_name=tool_name
    )[0]


def _denying_private() -> SecurityConfig:
    rule = PermissionRule("file_read", "private/*", PermissionAction.DENY)
    return SecurityConfig(ruleset=merge(DEFAULT_RULESET, (rule,)))


@POSIX_ONLY
async def test_a_credential_the_read_tool_opens_through_a_whitespace_name_is_never_allowed(workspace: Path) -> None:
    names = _link_every_whitespace_name(workspace, ".env")

    opened = [name for name in names if CREDENTIAL in await _read(name)]
    allowed = [name for name in opened if _verdict({"paths": [name]}, workspace) == PermissionAction.ALLOW]

    assert len(opened) > len(names) // 2, "the read tool opens most of the names, so the corpus exercises the verdict"
    assert not allowed, f"the engine allowed reading .env through the names {allowed!r}"


@POSIX_ONLY
async def test_a_deny_rule_covers_the_file_a_whitespace_name_opens(workspace: Path) -> None:
    names = _link_every_whitespace_name(workspace, "private/plan.md")

    opened = [name for name in names if PLAN in await _read(name)]
    not_denied = [
        name for name in opened if _verdict({"paths": [name]}, workspace, _denying_private()) != PermissionAction.DENY
    ]

    assert opened
    assert not not_denied, f"the rule on private/* did not cover the names {not_denied!r}"


@POSIX_ONLY
async def test_a_search_named_by_a_whitespace_link_is_judged_like_the_file(workspace: Path) -> None:
    names = _link_every_whitespace_name(workspace, ".env")

    opened = [name for name in names if CREDENTIAL in await _search(name)]
    args = [{"pattern": CREDENTIAL, "path": name} for name in opened]
    allowed = [a["path"] for a in args if _verdict(a, workspace, tool_name="grep_tool") == PermissionAction.ALLOW]

    assert opened
    assert not allowed, f"the engine allowed searching .env through the names {allowed!r}"


@pytest.mark.parametrize("typed", [" a.txt", "a.txt ", " a.txt ", "\ta.txt\n", "\u00a0a.txt\u3000"])
def test_stray_whitespace_around_an_ordinary_name_keeps_the_verdict_of_the_name(workspace: Path, typed: str) -> None:
    (workspace / "a.txt").write_text("plain")

    assert (
        _verdict({"paths": [typed]}, workspace) == _verdict({"paths": ["a.txt"]}, workspace) == PermissionAction.ALLOW
    )


@pytest.mark.parametrize(
    "typed", [" private/plan.md", "private/plan.md ", " private/plan.md:1-5 ", "\tprivate/plan.md\n"]
)
def test_stray_whitespace_around_a_denied_name_is_still_denied(workspace: Path, typed: str) -> None:
    assert _verdict({"paths": [typed]}, workspace, _denying_private()) == PermissionAction.DENY
