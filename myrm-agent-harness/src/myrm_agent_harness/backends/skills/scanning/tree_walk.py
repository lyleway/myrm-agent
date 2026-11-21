"""Bounded, complete walk over the files of a skill directory that a package manager may read.

A package manager reads a manifest wherever a workspace glob or a ``file:`` dependency points, so the walk has no
depth cap and enters every folder except ``node_modules``; what bounds it is the number of entries. A folder it cannot
list and a tree that exceeds the bound are yielded as problems, never swallowed, so a caller that audits what the walk
finds cannot mistake a partial look for a clean one.

[INPUT]
- (stdlib: os, collections, itertools, operator, dataclasses, pathlib)

[OUTPUT]
- MAX_ENTRIES: how many directory entries one walk looks at before it gives up
- WalkedFile: a file, or a symbolic link (never followed), the caller asked for by name
- WalkProblem: a part of the tree the walk could not examine, with the reason as the tail of a sentence
- walk_tree(): breadth-first walk yielding the wanted files and the problems

[POS]
File discovery for the readers of a skill's package manifests on disk, so that what they examine is what an installer
reads (every folder, at any depth) and a partial look is reported as one.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import islice
from operator import attrgetter
from pathlib import Path

MAX_ENTRIES = 100_000

# A dependency tree is not the skill's own file set, and one can hold more entries than the whole budget.
_SKIPPED_DIRS = frozenset({"node_modules"})

_UNLISTABLE = "a folder cannot be listed"
_UNEXAMINABLE = "a folder entry cannot be examined"


@dataclass(frozen=True, slots=True)
class WalkedFile:
    """A wanted file of the tree; a symbolic link is reported as one, not followed."""

    path: Path
    relative: str
    is_symlink: bool


@dataclass(frozen=True, slots=True)
class WalkProblem:
    """A part of the tree the walk could not examine, so what lies there is unknown.

    ``relative`` is the folder concerned (empty when the whole tree is); ``reason`` completes a sentence about what
    the caller could not do.
    """

    relative: str
    reason: str


def walk_tree(root: Path, wanted: Callable[[str], bool]) -> Iterator[WalkedFile | WalkProblem]:
    """Yield the files under *root* whose name *wanted* accepts, folder by folder in name order, then the problems met.

    Every entry listed counts against ``MAX_ENTRIES``; the entry that exceeds it ends the walk with a problem for the
    whole tree. A folder that cannot be listed is a problem for that folder, and the walk goes on with the others.
    """
    pending: deque[Path] = deque([root])
    seen = 0
    while pending:
        directory = pending.popleft()
        relative = str(directory.relative_to(root))
        try:
            with os.scandir(directory) as scan:
                # One entry past the budget is enough to know the folder overruns it; the rest is never read.
                entries = sorted(islice(scan, MAX_ENTRIES - seen + 1), key=attrgetter("name"))
        except OSError:
            yield WalkProblem(relative, _UNLISTABLE)
            continue
        for entry in entries:
            seen += 1
            if seen > MAX_ENTRIES:
                yield WalkProblem("", f"the folder holds more than {MAX_ENTRIES:,} entries")
                return
            if entry.name in _SKIPPED_DIRS:
                continue
            try:
                is_symlink = entry.is_symlink()
                is_directory = entry.is_dir(follow_symlinks=False)
                is_regular = entry.is_file(follow_symlinks=False)
            except OSError:
                yield WalkProblem(relative, _UNEXAMINABLE)
                continue
            if is_directory:
                pending.append(Path(entry.path))
            elif (is_symlink or is_regular) and wanted(entry.name):
                path = Path(entry.path)
                yield WalkedFile(path, str(path.relative_to(root)), is_symlink)
