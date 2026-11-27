"""The group chats a Feishu channel has seen a typed message from.

[INPUT]
- (none)

[OUTPUT]
- GroupChatMemory: Bounded record of the chat ids that typed messages reported as group chats.

[POS]
A card button click names its chat (``open_chat_id``) but not the kind of chat, while a typed message names both. The
channel remembers the group chats its typed messages came from, so a click is judged by the chat it happened in.
"""

from __future__ import annotations

from collections import OrderedDict

_DEFAULT_CAPACITY = 2048


class GroupChatMemory:
    """Group chat ids in the order they were last seen; the longest unseen is forgotten once ``capacity`` is exceeded."""

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._chats: OrderedDict[str, None] = OrderedDict()

    def remember(self, chat_id: str) -> None:
        """Record that ``chat_id`` is a group chat; an empty id names no chat and is ignored."""
        if not chat_id:
            return
        self._chats[chat_id] = None
        self._chats.move_to_end(chat_id)
        if len(self._chats) > self._capacity:
            self._chats.popitem(last=False)

    def __contains__(self, chat_id: object) -> bool:
        return chat_id in self._chats
