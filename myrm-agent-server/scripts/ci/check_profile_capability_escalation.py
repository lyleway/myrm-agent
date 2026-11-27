#!/usr/bin/env python3
"""Profile Capability Escalation CI Gate.

Audits the built-in security profiles so that no Pull Request silently relaxes the least-privilege baseline:
the Read Only profile denies every mutating action, the Workspace profile never allows shell, code or MCP
execution outright, neither enables YOLO mode, and every profile lists its capabilities explicitly.

[INPUT]
- app.services.security.profile_manager::_BUILTIN_PROFILES (POS: the profile definitions the server creates)

[OUTPUT]
- Exit 0: All built-in profiles comply with least-privilege security baseline.
- Exit 1: Privilege escalation or security policy violation detected.

[POS]
CI gate script executed during server-architecture checks. It audits the profiles as the server evaluates them,
not their source text: a profile may take values from a function call (the Read Only profile's permissions),
which a literal parse cannot read.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parent.parent.parent

# Each of these writes files, runs code or acts in the browser, so the Read Only profile must deny it.
_READONLY_DENIED = (
    "file_write",
    "file_edit",
    "file_delete",
    "shell_exec",
    "code_interpreter",
    "browser_evaluate",
    "browser_fill",
    "browser_upload",
    "browser_download",
)
# The Workspace profile leaves these to an approval (or denies them); it must never allow them outright.
_WORKSPACE_NEVER_ALLOWED = ("shell_exec", "code_interpreter", "mcp_invoke")


def load_builtin_profiles() -> Sequence[Mapping[str, object]]:
    """The profile definitions the server creates, as the server evaluates them."""
    # Run as a script, the interpreter's path starts at scripts/ci, so the server package needs its root added.
    if str(_SERVER_ROOT) not in sys.path:
        sys.path.insert(0, str(_SERVER_ROOT))
    from app.services.security.profile_manager import _BUILTIN_PROFILES

    return _BUILTIN_PROFILES


def _audit_readonly(config: Mapping[str, object]) -> list[str]:
    permissions = config.get("permissions", {})
    if not isinstance(permissions, dict):
        return ["Profile 'readonly': permissions must be a dict"]
    violations = [
        f"Profile 'readonly': '{action}' must be 'deny', got '{permissions.get(action)}'"
        for action in _READONLY_DENIED
        if permissions.get(action) != "deny"
    ]
    if config.get("yoloModeEnabled") is True:
        violations.append("Profile 'readonly': yoloModeEnabled must be False")
    return violations


def _audit_workspace(config: Mapping[str, object]) -> list[str]:
    permissions = config.get("permissions", {})
    if not isinstance(permissions, dict):
        return []
    violations = [
        f"Profile 'workspace': '{action}' must not be 'allow'"
        for action in _WORKSPACE_NEVER_ALLOWED
        if permissions.get(action) == "allow"
    ]
    if config.get("yoloModeEnabled") is True:
        violations.append("Profile 'workspace': yoloModeEnabled must be False")
    return violations


def audit_profiles(profiles: Sequence[Mapping[str, object]]) -> list[str]:
    """The baseline violations in ``profiles``; empty when every profile conforms."""
    if not profiles:
        # An audit with nothing to read would pass whatever the server creates.
        return ["No built-in security profiles to audit"]

    violations: list[str] = []
    for profile in profiles:
        key = str(profile.get("profile_key", "unknown"))
        config = profile.get("config_json")
        if not isinstance(config, dict):
            violations.append(f"Profile '{key}': missing or invalid config_json")
            continue
        if key == "readonly":
            violations.extend(_audit_readonly(config))
        elif key == "workspace":
            violations.extend(_audit_workspace(config))
        if not isinstance(config.get("capabilities"), list):
            violations.append(f"Profile '{key}': capabilities must be an explicit list")
    return violations


def main() -> int:
    print("🔍 Auditing built-in profiles for Capability Escalation...")
    violations = audit_profiles(load_builtin_profiles())

    if violations:
        print("❌ Profile Capability Escalation CI Gate FAILED:")
        for v in violations:
            print(f"  - {v}")
        return 1

    print("✅ Profile Capability Escalation CI Gate PASSED (all profiles conform to baseline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
