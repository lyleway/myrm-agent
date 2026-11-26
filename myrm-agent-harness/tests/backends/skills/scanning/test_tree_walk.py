"""Tests for the bounded walk over a skill directory.

[INPUT]
- backends.skills.scanning.tree_walk::walk_tree, WalkedFile, WalkLink, WalkProblem, MAX_ENTRIES (POS: the walk under test)

[OUTPUT]
- Fails when the walk stops at a depth, enters a folder other than node_modules, follows a symbolic link, says nothing of a
  link that leaves the skill, loses a folder it cannot list or a tree over the entry budget without a problem, or reads a
  huge folder past the budget.

[POS]
The lifecycle-script audit and the dependency extractor both rely on this walk to see every file an installer could
read, and to say so when it could not.
"""

from __future__ import annotations

import errno
import itertools
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from myrm_agent_harness.backends.skills.scanning import tree_walk
from myrm_agent_harness.backends.skills.scanning.tree_walk import WalkedFile, WalkLink, WalkProblem, walk_tree
from tests.support.symlinks import make_symlink


def _is_json(name: str) -> bool:
    return name.endswith(".json")


def _local(relative: str) -> str:
    return str(Path(relative))


def _touch(root: Path, *relatives: str) -> None:
    for relative in relatives:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")


def _found(root: Path) -> list[str]:
    return [item.relative for item in walk_tree(root, _is_json) if isinstance(item, WalkedFile)]


def _fail_listing(monkeypatch: pytest.MonkeyPatch, folder: str) -> None:
    """Make ``os.scandir`` refuse the folder with that name, as a permission error does."""
    real_scandir = os.scandir

    def scandir(path: Path) -> AbstractContextManager[Iterator[os.DirEntry[str]]]:
        if Path(path).name == folder:
            raise PermissionError(errno.EACCES, "denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)


def test_files_come_folder_by_folder_in_name_order_at_any_depth(tmp_path: Path) -> None:
    _touch(tmp_path, "b.json", "a/x.json", "a/b/y.json", "a/b/c/d/e/f/g/z.json")

    assert _found(tmp_path) == [_local(p) for p in ("b.json", "a/x.json", "a/b/y.json", "a/b/c/d/e/f/g/z.json")]


def test_only_files_the_caller_wants_are_yielded(tmp_path: Path) -> None:
    _touch(tmp_path, "keep.json")
    (tmp_path / "skip.txt").write_text("", encoding="utf-8")

    assert _found(tmp_path) == ["keep.json"]


@pytest.mark.parametrize("folder", [".git", ".hidden", "venv", ".venv", "__pycache__", "dist", "build", ".tox"])
def test_node_modules_is_the_only_folder_not_entered(tmp_path: Path, folder: str) -> None:
    _touch(tmp_path, "node_modules/dep/p.json", f"{folder}/p.json")

    assert _found(tmp_path) == [_local(f"{folder}/p.json")]


def test_a_symlink_to_a_file_is_reported_as_one_when_wanted_and_never_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _touch(outside, "inside.json")
    root = tmp_path / "root"
    root.mkdir()
    make_symlink(root / "link.json", outside / "inside.json")
    make_symlink(root / "other.txt", outside / "inside.json")

    assert list(walk_tree(root, _is_json)) == [WalkedFile(root / "link.json", "link.json", True)]


def test_a_symlink_to_a_folder_outside_the_root_is_reported_and_never_entered(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _touch(outside, "inside.json")
    root = tmp_path / "root"
    _touch(root, "a/b/seen.json")
    make_symlink(root / "a" / "b" / "exit", outside)
    make_symlink(root / "up", tmp_path)

    assert list(walk_tree(root, _is_json)) == [
        WalkLink("up"),
        WalkLink(_local("a/b/exit")),
        WalkedFile(root / "a" / "b" / "seen.json", _local("a/b/seen.json"), False),
    ]


def test_a_symlink_to_a_folder_inside_the_root_is_neither_entered_nor_reported(tmp_path: Path) -> None:
    _touch(tmp_path, "real/seen.json")
    make_symlink(tmp_path / "alias", tmp_path / "real")
    make_symlink(tmp_path / "again", tmp_path)

    assert list(walk_tree(tmp_path, _is_json)) == [
        WalkedFile(tmp_path / "real" / "seen.json", _local("real/seen.json"), False)
    ]


def test_a_symlink_that_leads_nowhere_is_ignored(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    make_symlink(root / "dangling", tmp_path / "missing")
    make_symlink(root / "loop", root / "loop")

    assert list(walk_tree(root, _is_json)) == []


def test_a_wanted_symlink_to_a_folder_outside_the_root_stays_a_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    make_symlink(root / "wanted.json", outside)

    assert list(walk_tree(root, _is_json)) == [WalkedFile(root / "wanted.json", "wanted.json", True)]


def test_a_folder_that_cannot_be_listed_is_a_problem_and_the_walk_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _touch(tmp_path, "locked/hidden.json", "open/seen.json")
    _fail_listing(monkeypatch, "locked")

    assert list(walk_tree(tmp_path, _is_json)) == [
        WalkProblem("locked", "a folder cannot be listed"),
        WalkedFile(tmp_path / "open" / "seen.json", _local("open/seen.json"), False),
    ]


def test_a_root_that_cannot_be_listed_is_a_problem_for_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_listing(monkeypatch, tmp_path.name)

    assert list(walk_tree(tmp_path, _is_json)) == [WalkProblem(".", "a folder cannot be listed")]


class _FakeEntry:
    """The part of ``os.DirEntry`` the walk reads, with a file system error on demand."""

    def __init__(self, directory: str, name: str, *, fails: bool = False) -> None:
        self.name = name
        self.path = os.path.join(directory, name)
        self._fails = fails

    def is_symlink(self) -> bool:
        if self._fails:
            raise PermissionError(errno.EACCES, "denied")
        return False

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return False

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        return True


class _FakeScan:
    """A ``scandir`` result over the given entries."""

    def __init__(self, entries: Iterator[_FakeEntry]) -> None:
        self._entries = entries

    def __enter__(self) -> Iterator[_FakeEntry]:
        return self._entries

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_an_entry_the_walk_cannot_examine_is_a_problem_for_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = os.fspath(tmp_path)
    entries = [_FakeEntry(directory, "a.json"), _FakeEntry(directory, "b.json", fails=True)]
    monkeypatch.setattr(os, "scandir", lambda path: _FakeScan(iter(entries)))

    assert list(walk_tree(tmp_path, _is_json)) == [
        WalkedFile(tmp_path / "a.json", "a.json", False),
        WalkProblem(".", "a folder entry cannot be examined"),
    ]


def test_a_tree_over_the_entry_budget_ends_with_one_problem_for_the_whole_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tree_walk, "MAX_ENTRIES", 4)
    _touch(tmp_path, "x.json", "a/y.json", "a/z.json", "w.json")

    items = list(walk_tree(tmp_path, _is_json))

    assert items[-1] == WalkProblem("", "the folder holds more than 4 entries")
    assert sum(isinstance(item, WalkProblem) for item in items) == 1


def test_a_tree_of_exactly_the_budget_is_walked_in_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tree_walk, "MAX_ENTRIES", 4)
    _touch(tmp_path, "x.json", "a/y.json", "a/z.json")

    assert _found(tmp_path) == [_local(p) for p in ("x.json", "a/y.json", "a/z.json")]
    assert all(isinstance(item, WalkedFile) for item in walk_tree(tmp_path, _is_json))


def test_a_folder_larger_than_the_budget_is_never_read_past_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = os.fspath(tmp_path)

    def endless() -> Iterator[_FakeEntry]:
        for index in itertools.count():
            assert index <= 1_000, "the walk read past the entry budget"
            yield _FakeEntry(directory, f"f{index:06d}")

    monkeypatch.setattr(tree_walk, "MAX_ENTRIES", 10)
    monkeypatch.setattr(os, "scandir", lambda path: _FakeScan(endless()))

    assert list(walk_tree(tmp_path, lambda name: False)) == [WalkProblem("", "the folder holds more than 10 entries")]
