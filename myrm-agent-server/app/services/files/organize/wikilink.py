"""Rewrite Obsidian-style wikilinks after file moves.

[INPUT]
- workspace 根路径 + moved_files (src,dst) 绝对路径对

[OUTPUT]
- rewrite_wikilinks_in_tree: 扫描 workspace 内 *.md 并重写 [[wikilink]] 目标

[POS]
Apply/rollback 后的 Obsidian 双链一致性维护。stem 与相对路径双映射。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

_TARGET_END_RE = re.compile(r"[\]|#]")
_ANCHOR_END_RE = re.compile(r"[\]|]")
_BRACKET_RE = re.compile(r"\]")


class _NextMark:
    """The first position at or after an index that holds a character of a set, remembered for the next question.

    No character of the set lies between the index asked and the position found, so the same answer serves every
    later question that falls inside that stretch.
    """

    __slots__ = ("_asked_from", "_found", "_pattern", "_text")

    def __init__(self, pattern: re.Pattern[str], text: str) -> None:
        self._pattern = pattern
        self._text = text
        self._asked_from = 0
        self._found = -1

    def at_or_after(self, index: int) -> int:
        """The position of the first mark at or after ``index``; the length of the text when there is none."""
        if not self._asked_from <= index <= self._found:
            match = self._pattern.search(self._text, index)
            self._asked_from = index
            self._found = match.start() if match else len(self._text)
        return self._found


def rewrite_wikilinks_in_tree(workspace: str, moved_files: list[tuple[str, str]]) -> int:
    """Update [[wikilink]] targets in markdown files under workspace. Returns files updated."""
    if not moved_files:
        return 0

    ws = Path(os.path.realpath(os.path.expanduser(workspace)))
    path_map: dict[str, str] = {}
    for src, dst in moved_files:
        src_rel = _as_posix_relpath(ws, src)
        dst_rel = _as_posix_relpath(ws, dst)
        src_stem = Path(src_rel).stem
        dst_stem = Path(dst_rel).stem
        path_map[src_rel] = dst_rel
        path_map[src_stem] = dst_stem

    updated = 0
    for md_path in ws.rglob("*.md"):
        if not md_path.is_file():
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        new_text = _rewrite_text(text, path_map)
        if new_text != text:
            md_path.write_text(new_text, encoding="utf-8")
            updated += 1
    return updated


def _wikilinks(text: str) -> Iterator[tuple[int, int, int]]:
    """The ``(start, target_end, end)`` of each ``[[target#anchor|alias]]`` in ``text``.

    The target is not empty and holds none of ``]|#``. An anchor (``#`` and what follows) holds neither ``]`` nor
    ``|``, an alias (``|`` and what follows) no ``]``, and neither is empty. Every part stops at a ``]``, and the link
    closes there or not at all, so the ``[[`` ahead of the same target end fail alike and the scan resumes at that end.
    A pattern that starts over from each of them reads to the end of the part once per ``[[``: 4,000 of them take half
    a second.
    """
    length = len(text)
    target_ends = _NextMark(_TARGET_END_RE, text)
    anchor_ends = _NextMark(_ANCHOR_END_RE, text)
    brackets = _NextMark(_BRACKET_RE, text)
    position = 0
    while (start := text.find("[[", position)) != -1:
        target_end = target_ends.at_or_after(start + 2)
        if target_end == length:
            return  # no "]" ahead: no later link closes either
        position = target_end
        if target_end == start + 2:
            continue
        end = _link_end(text, target_end, anchor_ends, brackets)
        if end != -1:
            yield start, target_end, end
            position = end


def _link_end(text: str, target_end: int, anchor_ends: _NextMark, brackets: _NextMark) -> int:
    """The position after the ``]]`` that closes the link whose target ends at ``target_end``; -1 when none does."""
    index = target_end
    if text[index] == "#":
        index = anchor_ends.at_or_after(index + 1)
        if index < target_end + 2 or index == len(text):
            return -1  # an empty anchor, or no "]" ahead
    if text[index] == "|":
        bracket = brackets.at_or_after(index + 1)
        if bracket < index + 2 or bracket == len(text):
            return -1  # an empty alias, or no "]" ahead
        index = bracket
    return index + 2 if text.startswith("]]", index) else -1


def _rewrite_text(text: str, path_map: dict[str, str]) -> str:
    pieces: list[str] = []
    last_end = 0
    for start, target_end, end in _wikilinks(text):
        target = text[start + 2 : target_end].strip()
        mapped = path_map.get(target)
        if mapped is None:
            mapped = path_map.get(Path(target).stem)
        if mapped is None:
            continue
        display = Path(mapped).stem if "/" not in mapped and "\\" not in mapped else mapped
        pieces.append(text[last_end:start])
        pieces.append(f"[[{display}{text[target_end : end - 2]}]]")
        last_end = end
    pieces.append(text[last_end:])
    return "".join(pieces)


def _as_posix_relpath(workspace: Path, path: str) -> str:
    resolved = Path(os.path.realpath(os.path.expanduser(path)))
    return resolved.relative_to(workspace).as_posix()
