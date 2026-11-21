"""Request-scoped security overlays layered over the persisted securityConfig.

[INPUT]
- myrm_agent_harness.agent.security.config::read_only_permissions, with_restriction
  (POS: security config parsing; pure functions)
- app.remote_access.tool_policy::merge_remote_security_overlay (POS: remote admission appended as one restriction layer)

[OUTPUT]
- SESSION_PRESETS / apply_session_preset: chat-session security presets (accept_edits: asks and denials plus auto
  mode; explore: the read-only permission keys)
- YoloGrant: a session YOLO grant (the moment it was turned on, how long it lasts)
- SecurityOverlaySpec: the overlay inputs of one request; ``apply`` layers them over a persisted securityConfig

[POS]
The persisted securityConfig is reloaded before every stream turn so a Settings change takes effect mid-session.
The overlays that belong to the request (session preset, session YOLO grant and approval window, remote-exposed
admission) are kept as data on the agent and re-applied on top of every reload, so a saved config can never loosen
or drop them. A value written into the config dict itself would not survive the reload.
A preset's permissions and remote admission are restrictions: each is appended to the config's ``restrictions`` as
one more layer, judged on its own beside the saved permissions, so the stricter verdict stands and no overlay can
reopen what the saved config asks about or denies. The settings an overlay turns on (auto mode, a YOLO grant, the
approval window) replace the saved value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from myrm_agent_harness.agent.security.config import read_only_permissions, with_restriction
from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class SessionPreset:
    """What choosing a chat-session preset does to a request's security config."""

    restriction: Mapping[str, str]
    settings: Mapping[str, object] = field(default_factory=dict)
    # The permissions a /learn authoring turn leaves out of the restriction: the turn exists to save a skill.
    learn_exempt: frozenset[str] = frozenset()


SESSION_PRESETS: Mapping[str, SessionPreset] = {
    "accept_edits": SessionPreset(
        restriction={
            "shell_exec": "ask",
            "code_interpreter": "ask",
            "browser_evaluate": "deny",
            "browser_upload": "ask",
            "browser_download": "ask",
            "browser_fill": "ask",
            "skill_manage": "ask",
            "cron_manage": "ask",
            "mcp_invoke": "ask",
            "invoke_external_agent": "ask",
        },
        settings={"autoModeEnabled": True},
    ),
    "explore": SessionPreset(restriction=read_only_permissions(), learn_exempt=frozenset({"skill_manage"})),
}


def apply_session_preset(
    base: dict[str, object] | None,
    preset: str | None,
    *,
    learn_authoring: bool = False,
) -> dict[str, object] | None:
    """Layer a chat-session security preset over the base config dict.

    When *preset* is ``None``, ``"hitl"`` (the default: every call that needs approval asks) or unknown, the base
    config is returned unchanged. Otherwise the preset's restriction is appended to the base's ``restrictions``
    and its settings replace the base's; *base* itself is never modified.
    """
    session_preset = SESSION_PRESETS.get(preset) if preset else None
    if session_preset is None:
        return base
    restriction = {
        permission: action
        for permission, action in session_preset.restriction.items()
        if not (learn_authoring and permission in session_preset.learn_exempt)
    }
    return {**with_restriction(base, restriction), **session_preset.settings}


class YoloGrant(BaseModel):
    """A session YOLO grant: the moment it was turned on and how long it lasts (``None``: until turned off)."""

    model_config = ConfigDict(frozen=True)

    enabled_at: float
    timeout_seconds: int | None = Field(default=None, gt=0)


class SecurityOverlaySpec(BaseModel):
    """The security overlays of one request, as plain data that survives serialization."""

    model_config = ConfigDict(frozen=True)

    preset: str | None = None
    trust_zone: str | None = None
    admission_path: str | None = None
    learn_authoring: bool = False
    yolo: YoloGrant | None = None
    approval_window_seconds: int | None = Field(default=None, gt=0)

    def apply(self, base: dict[str, object] | None) -> dict[str, object] | None:
        """Layer the request overlays over *base*: preset, session grants, remote admission.

        Remote admission comes after the grants so a remote-exposed request never keeps a session YOLO.
        """
        from app.remote_access.tool_policy import merge_remote_security_overlay

        config = self._with_grants(apply_session_preset(base, self.preset, learn_authoring=self.learn_authoring))
        return merge_remote_security_overlay(
            config,
            trust_zone=self.trust_zone,
            admission_path=self.admission_path,
        )

    def _with_grants(self, config: dict[str, object] | None) -> dict[str, object] | None:
        """*config* plus this request's approval window and YOLO grant; the grant replaces any stored YOLO timer."""
        if self.yolo is None and self.approval_window_seconds is None:
            return config
        granted = dict(config) if config else {}
        if self.approval_window_seconds is not None:
            granted["approvalTimeoutSeconds"] = self.approval_window_seconds
        if self.yolo is not None:
            granted["yolo_mode_enabled"] = True
            granted["yolo_mode_enabled_at"] = self.yolo.enabled_at
            granted["yolo_mode_timeout"] = self.yolo.timeout_seconds
        return granted


__all__ = ["SESSION_PRESETS", "SecurityOverlaySpec", "SessionPreset", "YoloGrant", "apply_session_preset"]
