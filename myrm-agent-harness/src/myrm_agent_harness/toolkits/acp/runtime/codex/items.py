"""The items of a turn that more than one reader needs: what a file change does, and the items seen so far.

[INPUT]
- toolkits.acp.runtime.codex.rpc::Notification (POS: A JSON-RPC peer over the stdio of Codex's app server)
- toolkits.acp.runtime.codex.wire::Json, as_list, as_obj, as_str (POS: Typed reads of the decoded JSON that Codex's app server sends)

[OUTPUT]
- FileChange, file_changes, changes_summary: the changes of a ``fileChange`` item and one line telling them
- ItemBook: the items of the notifications seen so far, by turn and id

[POS]
Shared by ``turn_events`` (which tells the host what an item is doing) and ``approvals`` (which tells a person what an
approval request is about: the request names only the item, the item holds the patch).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from myrm_agent_harness.toolkits.acp.runtime.codex.rpc import Notification
from myrm_agent_harness.toolkits.acp.runtime.codex.wire import Json, as_list, as_obj, as_str

# A turn rarely has more than a few dozen items; the bound only keeps a runaway turn from growing the book.
_MAX_ITEMS = 512
_SUMMARY_CHARS = 80


@dataclass(frozen=True, slots=True)
class FileChange:
    """One file a patch touches: ``kind`` is ``add``, ``delete`` or ``update``; ``moved_to`` is set for a rename."""

    kind: str
    path: str
    diff: str
    moved_to: str = ""


def file_changes(item: Json) -> list[FileChange]:
    """The changes of a ``fileChange`` item."""
    changes: list[FileChange] = []
    for raw in as_list(item.get("changes")):
        change = as_obj(raw)
        kind = as_obj(change.get("kind"))
        changes.append(
            FileChange(
                kind=as_str(kind.get("type")) or "update",
                path=as_str(change.get("path")),
                diff=as_str(change.get("diff")),
                moved_to=as_str(kind.get("move_path")),
            )
        )
    return changes


def changes_summary(changes: Sequence[FileChange]) -> str:
    """One short line telling what a patch does (``update a.py, add b.py``)."""
    line = ", ".join(f"{change.kind} {change.path}" for change in changes)
    return line if len(line) <= _SUMMARY_CHARS else line[: _SUMMARY_CHARS - 1] + "…"


class ItemBook:
    """The items the server has announced, by turn and id; filled in the order the server sent them."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Json] = {}

    def observe(self, notification: Notification) -> None:
        """Keep the item of an ``item/started`` or ``item/completed`` notification (the later one replaces the earlier)."""
        if notification.method not in ("item/started", "item/completed"):
            return
        item = as_obj(notification.params.get("item"))
        item_id = as_str(item.get("id"))
        if not item_id:
            return
        key = (as_str(notification.params.get("turnId")), item_id)
        if key not in self._items and len(self._items) >= _MAX_ITEMS:
            del self._items[next(iter(self._items))]
        self._items[key] = item

    def get(self, turn_id: str, item_id: str) -> Json:
        """The item, or an empty one when the server has not announced it."""
        return self._items.get((turn_id, item_id), {})

    def clear(self) -> None:
        self._items.clear()
