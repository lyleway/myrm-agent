"""A path is read exactly as given when a grant resolves it, so a name with a space at an end is that name."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from myrm_agent_harness.agent.security.session_access import (
    get_session_access_roots,
    grant_directories_for_call,
    plan_directory_grants,
    resolve_grant_directory_path,
    set_session_access_roots,
)
from myrm_agent_harness.agent.security.types import PathPolicy

_POLICY = PathPolicy(forbidden_paths=frozenset(), access_roots=())

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot create names that end in a space")


@pytest.fixture(autouse=True)
def _no_session_roots() -> Iterator[None]:
    set_session_access_roots(())
    yield
    set_session_access_roots(())


def test_the_folder_planned_for_a_whitespace_named_link_is_the_folder_the_tool_opens(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "s.txt").write_text("x", encoding="utf-8")
    (workspace / "s.txt ").symlink_to(outside / "s.txt")

    roots = plan_directory_grants(
        {"paths": ["s.txt "]}, permission="file_read", policy=_POLICY, workspace_root=str(workspace)
    )

    assert [root.path for root in roots] == [str(outside.resolve())]


def test_a_folder_whose_name_ends_in_a_space_is_the_folder_granted_not_the_one_above_it(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    folder = tmp_path / "outside" / "d "
    workspace.mkdir()
    folder.mkdir(parents=True)
    (folder / "x.txt").write_text("x", encoding="utf-8")
    args: dict[str, object] = {"paths": [str(folder / "x.txt")]}

    planned = plan_directory_grants(args, permission="file_read", policy=_POLICY, workspace_root=str(workspace))
    granted = grant_directories_for_call(args, permission="file_read", policy=_POLICY, workspace_root=str(workspace))

    assert [root.path for root in planned] == granted == [str(folder.resolve())]
    assert [root.path for root in get_session_access_roots()] == [str(folder.resolve())]


def test_a_grant_target_is_read_exactly_as_given(tmp_path: Path) -> None:
    folder = tmp_path / "d "
    folder.mkdir()

    assert resolve_grant_directory_path(str(folder), None) == str(folder.resolve())
