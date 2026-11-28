"""Server-side session directory access persistence and ContextVar bootstrap.

[INPUT]
- myrm_agent_harness.agent.security.session_access (POS: runtime session grant ContextVar)
- app.services.chat.chat_service::ChatService (POS: chat row persistence)

[OUTPUT]
- bootstrap_session_access_roots / persist_chat_session_access_roots
- grant_chat_session_access_root / DirectoryGrantRefusedError
- revoke_chat_session_access_root (removes the root a path names, persisted)
- apply_directory_resume_grant (grants the folders an approved card offered, on top of the folders the chat already holds)
- is_directory_grant_allowed_for_deployment

[POS]
Server persistence and deployment-boundary gate for session-scoped directory grants.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace

from myrm_agent_harness.agent.security.session_access import (
    get_session_access_roots,
    grant_session_access_root,
    resolve_grant_directory_path,
    revoke_session_access_root,
    set_session_access_roots,
)
from myrm_agent_harness.agent.security.types import (
    AccessRoot,
    PathPolicy,
    _default_path_policy,
)

logger = logging.getLogger(__name__)


class DirectoryGrantRefusedError(ValueError):
    """A directory grant the deployment boundary or the path policy refuses (e.g. a write over a home directory)."""


def access_roots_to_json(roots: tuple[AccessRoot, ...]) -> list[dict[str, object]]:
    return [
        {
            "path": root.path,
            "writable": root.writable,
            "label": root.label,
            "source": root.source,
        }
        for root in roots
    ]


def access_roots_from_json(raw: object) -> tuple[AccessRoot, ...]:
    if not isinstance(raw, list):
        return ()
    roots: list[AccessRoot] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path_obj = item.get("path")
        if not isinstance(path_obj, str) or not path_obj.strip():
            continue
        writable_obj = item.get("writable")
        label_obj = item.get("label")
        source_obj = item.get("source")
        roots.append(
            AccessRoot(
                path=path_obj,  # exactly as saved: a folder whose own name ends in a space is not the one above it
                writable=bool(writable_obj) if isinstance(writable_obj, bool) else True,
                label=str(label_obj).strip() if isinstance(label_obj, str) else "",
                source=(str(source_obj).strip() if isinstance(source_obj, str) else "persisted"),
            )
        )
    return tuple(roots)


def bootstrap_session_access_roots(
    raw: object | None,
    *,
    workspace_dir: str | None = None,
) -> tuple[AccessRoot, ...]:
    roots = access_roots_from_json(raw)
    validated: list[AccessRoot] = []
    for root in roots:
        grant_path = resolve_grant_directory_path(root.path, workspace_dir)
        if not grant_path:
            continue
        if not is_directory_grant_allowed_for_deployment(
            grant_path,
            workspace_dir=workspace_dir,
            writable=root.writable,
        ):
            logger.warning(
                "Skipping persisted session access root outside deployment boundary: %s",
                grant_path,
            )
            continue
        validated.append(replace(root, path=grant_path))
    effective = tuple(validated)
    set_session_access_roots(effective)
    return effective


async def persist_chat_session_access_roots(
    chat_id: str,
    roots: tuple[AccessRoot, ...] | None = None,
) -> None:
    effective = roots if roots is not None else get_session_access_roots()
    from app.services.chat.chat_service import ChatService

    await ChatService.update_chat_fields(
        chat_id,
        {"session_access_roots": access_roots_to_json(effective)},
    )


async def _load_chat_roots(chat_id: str) -> tuple[AccessRoot, ...] | None:
    """Put the folders the chat's row holds into the context, exactly as saved; ``None`` when the chat does not exist.

    A request starts with no folders in its context, so a change that is saved back (a grant, a revoke) is made on top of
    these: saving the change alone would replace the folders the chat already holds.
    """
    from app.services.chat.chat_service import ChatService

    chat = await ChatService.get_chat_metadata(chat_id)
    if chat is None:
        return None
    persisted = access_roots_from_json(chat.session_access_roots)
    set_session_access_roots(persisted)
    return persisted


async def grant_chat_session_access_root(
    chat_id: str,
    raw_path: str,
    *,
    writable: bool = True,
    label: str = "",
    source: str = "desktop_drag_drop",
    workspace_dir: str | None = None,
) -> tuple[AccessRoot, ...]:
    """Add one session access root and persist to chat record.

    Raises ``DirectoryGrantRefusedError`` when the grant is refused; a folder the chat already holds (or one inside
    a held folder, at the access asked for) is not a refusal.
    """
    if await _load_chat_roots(chat_id) is None:
        return ()

    _apply_validated_grant(
        raw_path,
        writable=writable,
        source=source,
        workspace_dir=workspace_dir,
        policy=_default_path_policy(),
    )
    updated = get_session_access_roots()
    grant_path = resolve_grant_directory_path(raw_path, workspace_dir)
    if not _holds_grant(updated, grant_path, writable=writable):
        raise DirectoryGrantRefusedError(f"Folder access cannot be granted for this chat: {raw_path}")
    await persist_chat_session_access_roots(chat_id, updated)
    return updated


async def revoke_chat_session_access_root(
    chat_id: str,
    raw_path: str,
    *,
    workspace_dir: str | None = None,
) -> tuple[AccessRoot, ...]:
    """Remove one persisted session access root and sync the runtime ContextVar."""
    persisted = await _load_chat_roots(chat_id)
    if persisted is None:
        return ()

    updated = revoke_session_access_root(raw_path, workspace_root=workspace_dir)
    if updated == persisted:
        return persisted

    await persist_chat_session_access_roots(chat_id, updated)
    return updated


def _is_cloud_volume_deployment() -> bool:
    from myrm_agent_harness.runtime.paths.execution_paths import DEFAULT_PERSISTENT_ROOT, get_persistent_root

    return get_persistent_root() == DEFAULT_PERSISTENT_ROOT and os.path.isdir(DEFAULT_PERSISTENT_ROOT)


def _is_subpath(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent + os.sep)


def _holds_grant(roots: tuple[AccessRoot, ...], grant_path: str, *, writable: bool) -> bool:
    """Whether a held root already covers ``grant_path`` at the access asked for."""
    return bool(grant_path) and any(_is_subpath(grant_path, root.path) and (root.writable or not writable) for root in roots)


def is_directory_grant_allowed_for_deployment(
    grant_path: str,
    *,
    workspace_dir: str | None,
    writable: bool = True,
) -> bool:
    """Gate directory grants by deployment mode (cloud volume, local desktop).

    ``writable`` selects the mount mode the gate checks: a write grant is also refused when it
    contains a dangerous root (a home directory, a drive root), a read-only grant is not.
    """
    from myrm_agent_harness.api.hooks import (
        MountMode,
        MountSpec,
        validate_mount_spec,
    )

    allowed_boundaries: tuple[str, ...] = ()
    if _is_cloud_volume_deployment():
        from myrm_agent_harness.runtime.paths.execution_paths import get_persistent_root

        persistent_root = os.path.realpath(get_persistent_root())
        boundaries = [persistent_root]
        if workspace_dir:
            boundaries.append(os.path.realpath(os.path.expanduser(workspace_dir)))
        allowed_boundaries = tuple(boundaries)

    spec = MountSpec(source_path=grant_path, mode=MountMode.RW if writable else MountMode.RO)
    result = validate_mount_spec(spec, allowed_boundaries=allowed_boundaries)
    return result.is_valid


def _apply_validated_grant(
    raw_path: str,
    *,
    writable: bool,
    source: str,
    workspace_dir: str | None,
    policy: PathPolicy | None = None,
) -> bool:
    grant_path = resolve_grant_directory_path(raw_path, workspace_dir)
    if not grant_path:
        return False
    if not is_directory_grant_allowed_for_deployment(
        grant_path,
        workspace_dir=workspace_dir,
        writable=writable,
    ):
        logger.warning(
            "Directory grant rejected by deployment boundary: path=%s workspace=%s",
            grant_path,
            workspace_dir,
        )
        return False

    roots_before = get_session_access_roots()
    effective_policy = policy or _default_path_policy()
    updated = grant_session_access_root(
        AccessRoot(path=grant_path, writable=writable, source=source),
        policy=effective_policy,
        workspace_root=workspace_dir,
    )
    return updated != roots_before


def _offered_folders(resume_value: dict[str, object]) -> list[tuple[str, bool]]:
    """The ``(path, writable)`` of every folder an approved card offered and the user ticked the grant for, in card order."""
    decisions = resume_value.get("decisions")
    if not isinstance(decisions, list):
        return []
    offered: list[tuple[str, bool]] = []
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("type") != "approve":
            continue
        extensions = decision.get("extensions")
        if not isinstance(extensions, dict) or extensions.get("grantDirectory") is not True:
            continue
        grants = extensions.get("grantDirectories")
        if not isinstance(grants, list):
            continue
        for grant in grants:
            if not isinstance(grant, dict):
                continue
            path = grant.get("path")
            if isinstance(path, str) and path.strip():
                offered.append((path, grant.get("writable") is True))
    return offered


async def apply_directory_resume_grant(
    chat_id: str | None,
    resume_value: dict[str, object],
    *,
    workspace_dir: str | None = None,
) -> None:
    """Persist and bootstrap grants from path-ASK approval resume payloads.

    The grants are added to the folders the chat already holds, which are read only when a card offered one.
    """
    offered = _offered_folders(resume_value)
    if not offered:
        return
    if chat_id:
        await _load_chat_roots(chat_id)

    policy = _default_path_policy()
    granted = False
    for path, writable in offered:
        if _apply_validated_grant(
            path,
            writable=writable,
            source="path_ask_grant",
            workspace_dir=workspace_dir,
            policy=policy,
        ):
            granted = True

    if granted and chat_id:
        await persist_chat_session_access_roots(chat_id)
