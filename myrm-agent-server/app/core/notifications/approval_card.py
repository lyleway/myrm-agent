"""Approval notification card: localized copy, the request's reason, reply preview and route-aware decision buttons.

[INPUT]
- app.channels.i18n::channel_t (POS: localized channel copy catalog)
- app.channels.types::DraftTimeoutAction (POS: what happens when a held reply expires)
- app.channels.types.components::ActionButton, ButtonStyle, ComponentRow (POS: interactive card components)
- app.services.approvals.external_agent_permission::ACTION_TYPE (POS: the approval type of an external agent's permission request)
- app.services.approvals.outbound_draft::draft_code (POS: how a held reply is named in chat)
- app.services.channels.cp_egress_client::should_route_via_control_plane (POS: whether replies leave through the text-only control-plane egress)

[OUTPUT]
- APPROVAL_CLICK_CHANNELS: channels whose inbound parser turns a button click into the approval message the router resolves
- build_approval_card: (text, buttons) for one APPROVAL_REQUIRED event on one target channel

[POS]
Presentation of approval requests for IM notification targets. Functions over the event data and the delivery route.
The card says what is being asked: the request's ``reason`` as the registry publishes it, quoted and capped.
Every quoted text comes from a request or a held message, not from us, so it is shown without the syntax that
would make a chat platform ping a whole channel or label a link as something else.
A held channel reply (``outbound_draft``) shows the incoming message and the drafted text, so the
reviewer can decide without opening the app; where there are no buttons it also spells out the
``/draft`` commands that decide it in chat. An external agent's permission request names what it wants to do
(its ``reason``) under a header that says who is asking, never the internal action type.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import PurePath

from app.channels.i18n import channel_t
from app.channels.types import DraftTimeoutAction
from app.channels.types.components import ActionButton, ButtonStyle, ComponentRow
from app.services.approvals.external_agent_permission import ACTION_TYPE as EXTERNAL_AGENT_PERMISSION
from app.services.approvals.outbound_draft import draft_code
from app.services.channels.cp_egress_client import should_route_via_control_plane

# Telegram is the only channel whose inbound parser turns a button click into the ``approval:<decision>:<id>``
# message that the router resolves; a button on any other channel would do nothing when pressed. A click also has to
# reach this instance: control-plane egress carries plain text only, so no button is shown on a hosted channel.
APPROVAL_CLICK_CHANNELS: frozenset[str] = frozenset({"telegram"})

_OUTBOUND_DRAFT = "outbound_draft"
_INCOMING_PREVIEW_CHARS = 300
_REPLY_PREVIEW_CHARS = 800
_REASON_PREVIEW_CHARS = 500
_MAX_ATTACHMENT_NAMES = 5

# On Slack and Discord ``<!channel>``, ``<@id>`` and ``<url|label>`` are live, as are ``@everyone`` / ``@here`` and a
# markdown ``[label](url)``. A zero-width space right after the opening character keeps the text readable and makes
# none of them syntax.
_LIVE_MARKUP = re.compile(r"(<|\](?=\()|(?<!\w)@(?=(?:everyone|here)\b))", re.IGNORECASE)
_ZERO_WIDTH_SPACE = "\u200b"


def build_approval_card(
    data: Mapping[str, object],
    channel: str | None,
    locale: str | None,
) -> tuple[str, tuple[ComponentRow, ...]]:
    """Render one approval request for ``channel``.

    Approve/Deny buttons are attached only where a click can be resolved; every other route gets a
    pointer to the app, since a click there never reaches the approval handler. A held reply additionally
    names the ``/draft`` commands there, so the owner can decide by typing.
    """
    approval_id = str(data.get("approval_id") or "")
    action_type = str(data.get("action_type") or "action")
    if action_type == _OUTBOUND_DRAFT:
        content = _outbound_draft_content(data, locale)
    elif action_type == EXTERNAL_AGENT_PERMISSION:
        content = _permission_content(data, locale)
    else:
        content = _generic_content(data, action_type, locale)

    if approval_id and _click_reaches_router(channel):
        return content, (_decision_buttons(approval_id, locale),)
    hints = [_t(locale, "approval_card_open_app")]
    if approval_id and action_type == _OUTBOUND_DRAFT:
        hints.insert(0, _t(locale, "approval_draft_commands", code=draft_code(approval_id)))
    return "\n\n".join((content, *hints)), ()


def _click_reaches_router(channel: str | None) -> bool:
    """Whether a button sent to ``channel`` can be pressed and resolved: the channel parses clicks and the send stays local."""
    return channel in APPROVAL_CLICK_CHANNELS and not should_route_via_control_plane(channel)


def _t(locale: str | None, key: str, **kwargs: object) -> str:
    return str(channel_t(locale, key, **kwargs))


def _decision_buttons(approval_id: str, locale: str | None) -> tuple[ActionButton, ActionButton]:
    return (
        ActionButton(
            label=_t(locale, "approval_button_approve"),
            action_id=f"approval:approve:{approval_id}",
            style=ButtonStyle.PRIMARY,
        ),
        ActionButton(
            label=_t(locale, "approval_button_deny"),
            action_id=f"approval:deny:{approval_id}",
            style=ButtonStyle.DANGER,
        ),
    )


def _generic_content(data: Mapping[str, object], action_type: str, locale: str | None) -> str:
    header = "\n".join(
        (
            _t(locale, "approval_card_title", action=action_type),
            _t(locale, "approval_card_severity", severity=str(data.get("severity") or "medium")),
        )
    )
    reason = _preview(data.get("reason"), _REASON_PREVIEW_CHARS)
    return f"{header}\n\n{_quote(reason)}" if reason else header


def _permission_content(data: Mapping[str, object], locale: str | None) -> str:
    """What an external agent asks to do, in the words of the request."""
    title = _t(locale, "approval_permission_title")
    reason = _preview(data.get("reason"), _REASON_PREVIEW_CHARS)
    return f"{title}\n\n{_quote(reason)}" if reason else title


def _outbound_draft_content(data: Mapping[str, object], locale: str | None) -> str:
    payload = _mapping(data.get("payload"))
    sections = [_t(locale, "approval_draft_title")]

    incoming = _preview(payload.get("original_content"), _INCOMING_PREVIEW_CHARS)
    if incoming:
        sections.append("\n".join((_t(locale, "approval_draft_incoming"), _quote(incoming))))

    reply = _preview(payload.get("draft_content"), _REPLY_PREVIEW_CHARS)
    if reply:
        sections.append("\n".join((_t(locale, "approval_draft_reply"), _quote(reply))))

    names = _attachment_names(payload)
    if names:
        sections.append(_t(locale, "approval_draft_attachments", names=names))

    minutes = _minutes_left(data.get("expires_at"))
    if minutes is not None:
        auto_send = payload.get("draft_timeout_action") == DraftTimeoutAction.AUTO_SEND
        key = "approval_draft_expiry_send" if auto_send else "approval_draft_expiry_discard"
        sections.append(_t(locale, key, minutes=minutes))

    return "\n\n".join(sections)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _preview(value: object, limit: int) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return _defang(text if len(text) <= limit else f"{text[:limit].rstrip()}…")


def _defang(text: str) -> str:
    return _LIVE_MARKUP.sub(f"\\1{_ZERO_WIDTH_SPACE}", text)


def _quote(text: str) -> str:
    """Quote every line, so a multi-line message stays visibly separate from the surrounding labels."""
    return "\n".join(f"> {line}" for line in text.splitlines())


def _attachment_names(payload: Mapping[str, object]) -> str:
    """File names of the held reply's attachments; a URL is never shown because it may embed an access token."""
    media = _mapping(payload.get("outbound")).get("media")
    names: list[str] = []
    for item in media if isinstance(media, list) else ():
        entry = _mapping(item)
        name = entry.get("filename") or _basename(entry.get("path")) or entry.get("media_type")
        if isinstance(name, str) and name:
            names.append(name)
    shown = ", ".join(names[:_MAX_ATTACHMENT_NAMES])
    return _defang(f"{shown}, …" if len(names) > _MAX_ATTACHMENT_NAMES else shown)


def _basename(path: object) -> str | None:
    return PurePath(path).name if isinstance(path, str) and path else None


def _minutes_left(expires_at: object) -> int | None:
    """Whole minutes until the held reply expires, rounded up so a fresh draft never reads as zero."""
    if not isinstance(expires_at, str):
        return None
    try:
        deadline = datetime.fromisoformat(expires_at)
    except ValueError:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return max(1, math.ceil((deadline - datetime.now(timezone.utc)).total_seconds() / 60))
