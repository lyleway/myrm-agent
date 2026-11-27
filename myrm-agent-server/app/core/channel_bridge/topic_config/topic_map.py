"""[INPUT]
- .common (POS: shared primitives of the channel topic-config domain)

[OUTPUT]
- storage_key_of: the key a binding is stored under inside its chat
- binding_entry: the stored entry of one binding
- delete_binding: remove one binding from a map
- drop_bindings_of: remove every binding that names one of the given agents

[POS]
Pure operations on the stored topic map of a channel, ``{chat_id: {storage_key: entry}}``. They do no I/O and never
await, so a change built from them can be applied to the map as it is read under the write lock.
"""

from __future__ import annotations

from .common import _CHANNEL_LEVEL_KEY


def storage_key_of(thread_id: str | None) -> str:
    """The key a binding is stored under inside its chat: the thread, or the channel-level key for the chat itself."""
    return thread_id if thread_id is not None else _CHANNEL_LEVEL_KEY


def binding_entry(config: dict[str, object], chat_id: str, storage_key: str) -> dict[str, object] | None:
    """The stored entry of one binding; ``None`` when there is none or it is malformed."""
    group_topics = config.get(chat_id)
    entry = group_topics.get(storage_key) if isinstance(group_topics, dict) else None
    return entry if isinstance(entry, dict) else None


def delete_binding(config: dict[str, object], chat_id: str, storage_key: str) -> bool:
    """Remove one binding, and its chat when it was the chat's last; False when it was not stored."""
    group_topics = config.get(chat_id)
    if not isinstance(group_topics, dict) or storage_key not in group_topics:
        return False
    del group_topics[storage_key]
    if not group_topics:
        del config[chat_id]
    return True


def drop_bindings_of(config: dict[str, object], agent_ids: set[str]) -> list[tuple[str, str, str]]:
    """Remove every binding that names one of ``agent_ids``; the removed ones as ``(chat_id, storage_key, agent_id)``."""
    removed: list[tuple[str, str, str]] = []
    for chat_id, group_topics in list(config.items()):
        if not isinstance(group_topics, dict):
            continue
        for storage_key, entry in list(group_topics.items()):
            agent_id = entry.get("agentId") if isinstance(entry, dict) else None
            if isinstance(agent_id, str) and agent_id in agent_ids:
                delete_binding(config, chat_id, storage_key)
                removed.append((chat_id, storage_key, agent_id))
    return removed
