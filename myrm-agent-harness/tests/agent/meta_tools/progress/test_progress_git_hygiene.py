"""A plan written into a project under version control never shows up in the project's git."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from myrm_agent_harness.agent.meta_tools.progress.schemas import TodoItem, TodoStore
from myrm_agent_harness.agent.meta_tools.progress.storage import todos_path, write_todos_sync_to_workspace

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(repository: Path, *args: str) -> str:
    # The caller's own git settings (a global excludes file, config passed in the environment) must not decide what
    # git ignores here.
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_CONFIG")}
    env.update(HOME=str(repository), GIT_CONFIG_NOSYSTEM="1")
    completed = subprocess.run(
        ["git", *args], cwd=repository, env=env, capture_output=True, text=True, timeout=20, check=True
    )
    return completed.stdout


def _plan() -> TodoStore:
    return TodoStore(goal="Ship it", todos=[TodoItem(id="t1", content="Write the change")])


def test_git_neither_lists_nor_stages_the_plans_of_any_chat(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "mine.txt").write_text("keep\n", encoding="utf-8")

    write_todos_sync_to_workspace(str(tmp_path), _plan(), "chat_a")
    write_todos_sync_to_workspace(str(tmp_path), _plan(), "chat_b")
    write_todos_sync_to_workspace(str(tmp_path), _plan())

    assert todos_path(str(tmp_path), "chat_a").is_file()
    assert _git(tmp_path, "status", "--porcelain", "-uall").split() == ["??", "mine.txt"]

    _git(tmp_path, "add", "-A")
    assert _git(tmp_path, "ls-files").split() == ["mine.txt"]


def test_the_progress_directory_ignores_everything_in_it(tmp_path: Path) -> None:
    write_todos_sync_to_workspace(str(tmp_path), _plan(), "chat_a")

    assert (tmp_path / ".myrm" / "progress" / ".gitignore").read_text(encoding="utf-8") == "*\n"
