"""[INPUT]
- .common (POS: shared constants and helpers)
- .topic_map (POS: pure operations on the stored topic map)

[OUTPUT]
- BindingMixin
- topic_context: the TopicContext a stored binding entry stands for

[POS]
Topic manager mixin: topic resolve/bind/unbind and metadata sync. What a method needs from the outside world is awaited
before its change is built, and the change is applied to the stored map under the write lock.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum

from app.channels.types import DraftTimeoutAction, IdentityScopeMode, ReplyMode, TopicContext

from .common import (
    _TOPIC_WORKSPACE_MUTUAL_EXCLUSIVE_MSG,
    _UNSET,
    _Unset,
    clean_identity_id,
    logger,
)
from .lifecycle import LifecycleMixin
from .policy import PolicyMixin
from .topic_map import binding_entry, delete_binding, storage_key_of


def _member[E: StrEnum](enum_type: type[E], stored: object, default: E) -> E:
    """The member of ``enum_type`` a stored value names; ``default`` when it names none."""
    try:
        return enum_type(str(stored))
    except ValueError:
        return default


def _minutes(stored: object, default: int) -> int:
    return int(stored) if isinstance(stored, (int, float, str)) else default


def topic_context(topic_id: str, entry: dict[str, object]) -> TopicContext:
    """The ``TopicContext`` a stored binding entry stands for; a stored mode that names no known one falls back to its default."""
    return TopicContext(
        topic_id=topic_id,
        agent_id=str(entry["agentId"]) if entry.get("agentId") else None,
        project_id=str(entry["projectId"]) if entry.get("projectId") else None,
        authorized_path=str(entry["authorizedPath"]) if entry.get("authorizedPath") else None,
        enabled=bool(entry.get("enabled", True)),
        bound_at=str(entry["boundAt"]) if entry.get("boundAt") else None,
        reply_mode=_member(ReplyMode, entry.get("replyMode"), ReplyMode.AUTO),
        draft_timeout_minutes=_minutes(entry.get("draftTimeoutMinutes"), 5),
        draft_timeout_action=_member(DraftTimeoutAction, entry.get("draftTimeoutAction"), DraftTimeoutAction.AUTO_REJECT),
        identity_scope=_member(IdentityScopeMode, entry.get("identityScope"), IdentityScopeMode.INHERIT),
        identity_id=str(entry["identityId"]) if entry.get("identityId") else None,
        identity_name=str(entry["identityName"]) if entry.get("identityName") else None,
        identity_revoked=bool(entry.get("identityRevoked", False)),
        completion_receipts=bool(entry.get("completionReceipts", True)),
        stall_nudge=bool(entry.get("stallNudge", False)),
    )


class BindingMixin(LifecycleMixin, PolicyMixin):
    # The default life of a new binding; ``SqlTopicManager.__init__`` sets it.
    _default_idle_timeout_h: int
    _default_max_age_h: int | None

    async def resolve_topic(
        self,
        channel: str,
        chat_id: str,
        thread_id: str | None,
    ) -> TopicContext | None:
        storage_key = storage_key_of(thread_id)
        entry = binding_entry(await self._load_config(channel), chat_id, storage_key)
        if entry is None:
            return None

        if self._is_expired(entry):
            if await self._remove_binding(channel, chat_id, storage_key, only_if=self._is_expired):
                logger.info("SqlTopicManager: expired binding %s/%s/%s auto-removed", channel, chat_id, storage_key)
            return None

        agent_id = entry.get("agentId")
        if isinstance(agent_id, str) and agent_id and not await self._is_channel_bindable_agent(agent_id):
            if await self._remove_binding(
                channel, chat_id, storage_key, only_if=lambda stored: stored.get("agentId") == agent_id
            ):
                logger.info(
                    "SqlTopicManager: unbindable agent %s binding %s/%s/%s auto-removed",
                    agent_id,
                    channel,
                    chat_id,
                    storage_key,
                )
            return None

        await self._touch_active(channel, chat_id, storage_key)
        return topic_context(thread_id or chat_id, entry)

    async def _remove_binding(
        self,
        channel: str,
        chat_id: str,
        storage_key: str,
        *,
        only_if: Callable[[dict[str, object]], bool],
    ) -> bool:
        """Housekeeping: remove a binding, provided ``only_if`` still holds for it as stored.

        The caller judged a copy it read earlier; the binding may have been rebound since, and a fresh one stays.
        """

        def remove(config: dict[str, object]) -> bool | None:
            entry = binding_entry(config, chat_id, storage_key)
            if entry is None or not only_if(entry):
                return None
            return delete_binding(config, chat_id, storage_key) or None

        return bool(await self._mutate_config(channel, remove, best_effort=True))

    async def bind_topic(
        self,
        channel: str,
        chat_id: str,
        thread_id: str | None,
        *,
        agent_id: str | None | _Unset = _UNSET,
        display_name: str | None = None,
        avatar_url: str | None = None,
        reply_mode: ReplyMode | _Unset = _UNSET,
        draft_timeout_minutes: int | _Unset = _UNSET,
        draft_timeout_action: DraftTimeoutAction | _Unset = _UNSET,
        project_id: str | None | _Unset = _UNSET,
        authorized_path: str | None | _Unset = _UNSET,
        identity_scope: IdentityScopeMode | _Unset = _UNSET,
        identity_id: str | None | _Unset = _UNSET,
        identity_name: str | None | _Unset = _UNSET,
        identity_revoked: bool | _Unset = _UNSET,
        completion_receipts: bool | _Unset = _UNSET,
        stall_nudge: bool | _Unset = _UNSET,
    ) -> TopicContext:
        """Create or update the binding of a topic; a field left unset keeps the value stored for it.

        What needs the outside world (the agent is bindable, the project or path is valid) is settled first, so the write
        lock is held only to merge the change into the stored entry.
        """
        sets: dict[str, object] = {}
        clears: list[str] = []

        if agent_id is not _UNSET:
            resolved_agent_id = None if agent_id is None else str(agent_id)
            if resolved_agent_id:
                resolved_agent_id = await self._resolve_agent_id(resolved_agent_id)
                await self._assert_channel_bindable_agent(resolved_agent_id)
            if resolved_agent_id is None:
                clears.append("agentId")
            else:
                sets["agentId"] = resolved_agent_id

        if project_id is not _UNSET and authorized_path is not _UNSET and project_id and authorized_path:
            raise ValueError(_TOPIC_WORKSPACE_MUTUAL_EXCLUSIVE_MSG)

        if reply_mode is not _UNSET:
            sets["replyMode"] = reply_mode.value
        if draft_timeout_minutes is not _UNSET:
            sets["draftTimeoutMinutes"] = draft_timeout_minutes
        if draft_timeout_action is not _UNSET:
            sets["draftTimeoutAction"] = draft_timeout_action.value
        if display_name is not None:
            sets["displayName"] = display_name
        if avatar_url is not None:
            sets["avatarUrl"] = avatar_url

        if project_id is not _UNSET:
            if project_id:
                from app.core.channel_bridge.topic_workspace_bind import assert_project_workspace

                await assert_project_workspace(str(project_id))
                sets["projectId"] = str(project_id)
                clears.append("authorizedPath")
            else:
                clears.append("projectId")
        if authorized_path is not _UNSET:
            if authorized_path:
                from app.core.channel_bridge.topic_workspace_bind import validate_authorized_path

                sets["authorizedPath"] = validate_authorized_path(str(authorized_path))
                clears.append("projectId")
            else:
                clears.append("authorizedPath")

        if identity_scope is not _UNSET:
            sets["identityScope"] = identity_scope.value
        if identity_id is not _UNSET:
            if identity_id:
                sets["identityId"] = clean_identity_id(str(identity_id))
            else:
                clears.append("identityId")
        if identity_name is not _UNSET:
            if identity_name:
                sets["identityName"] = str(identity_name)
            else:
                clears.append("identityName")
        if identity_revoked is not _UNSET:
            sets["identityRevoked"] = bool(identity_revoked)
        if completion_receipts is not _UNSET:
            sets["completionReceipts"] = bool(completion_receipts)
        if stall_nudge is not _UNSET:
            sets["stallNudge"] = bool(stall_nudge)

        storage_key = storage_key_of(thread_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        def bind(config: dict[str, object]) -> dict[str, object]:
            group_topics = config.get(chat_id)
            if not isinstance(group_topics, dict):
                group_topics = config[chat_id] = {}
            stored = group_topics.get(storage_key)
            entry = dict(stored) if isinstance(stored, dict) else {}
            entry["enabled"] = True
            entry["boundAt"] = entry.get("boundAt") or now_iso
            entry["lastActiveAt"] = now_iso
            entry["idleTimeoutH"] = self._default_idle_timeout_h
            if self._default_max_age_h is not None:
                entry["maxAgeH"] = self._default_max_age_h
            entry.update(sets)
            for key in clears:
                entry.pop(key, None)
            group_topics[storage_key] = entry
            return entry

        bound = await self._mutate_config(channel, bind)
        assert bound is not None, "a bind always writes its entry"
        return topic_context(thread_id or chat_id, bound)

    async def unbind_topic(
        self,
        channel: str,
        chat_id: str,
        thread_id: str | None,
    ) -> bool:
        storage_key = storage_key_of(thread_id)
        removed = await self._mutate_config(channel, lambda config: delete_binding(config, chat_id, storage_key) or None)
        if removed is None:
            return False

        from app.channels.routing.follow_up import drop_tracked_thread

        drop_tracked_thread(channel, chat_id, thread_id)
        return True

    async def sync_topic_metadata(
        self,
        channel: str,
        chat_id: str,
        thread_id: str | None,
        *,
        display_name: str | None = None,
        avatar_url: str | None = None,
    ) -> None:
        """Record the name and avatar the platform reports for a chat, so the UI lists it; writes only when one differs."""
        if display_name is None and avatar_url is None:
            return
        storage_key = storage_key_of(thread_id)

        def differs(entry: dict[str, object] | None) -> bool:
            stored = entry or {}
            return (display_name is not None and stored.get("displayName") != display_name) or (
                avatar_url is not None and stored.get("avatarUrl") != avatar_url
            )

        # Every inbound message of a named chat comes through here: the cached map answers the usual "nothing new"
        # without the write lock.
        if not differs(binding_entry(await self._load_config(channel), chat_id, storage_key)):
            return

        def sync(config: dict[str, object]) -> bool | None:
            group_topics = config.get(chat_id)
            if not isinstance(group_topics, dict):
                group_topics = config[chat_id] = {}
            entry = group_topics.get(storage_key)
            if not isinstance(entry, dict):
                entry = group_topics[storage_key] = {}
            if not differs(entry):
                return None
            if display_name is not None:
                entry["displayName"] = display_name
            if avatar_url is not None:
                entry["avatarUrl"] = avatar_url
            return True

        await self._mutate_config(channel, sync)
