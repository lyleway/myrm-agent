"""The profile capability gate audits the profiles the server creates and fails when one crosses its baseline.

[INPUT]
- scripts/ci/check_profile_capability_escalation.py (POS: the gate, loaded by path because scripts/ci is not a package)
- app.services.security.profile_manager::_BUILTIN_PROFILES (POS: the profiles the gate audits, through the gate)

[OUTPUT]
- that the built-in profiles conform, that each rule of the baseline fails on a profile that breaks it, that an audit
  with no profile to read fails, and that the script exits with failure naming the violation and runs standalone the
  way the gate script calls it

[POS]
In the gate script's shell chain an exception in one gate ends the run before every later gate starts, and a gate that
reads nothing passes everything. Running the audit here puts it under the architecture suite on its own, and the
mutated profiles show that it can fail.
"""

from __future__ import annotations

import copy
import importlib.util
import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

_GATE = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "check_profile_capability_escalation.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("profile_capability_gate_under_test", _GATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()

# The baseline spelled out here, not read from the gate: a test parametrized over the gate's own lists would lose a case
# together with the entry that a change dropped from them.
_READONLY_MUST_DENY = (
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
_WORKSPACE_MUST_NOT_ALLOW = ("shell_exec", "code_interpreter", "mcp_invoke")


def _profiles() -> list[dict[str, object]]:
    """A private copy of the server's profiles: a test edits it without touching the definitions themselves."""
    return copy.deepcopy([dict(profile) for profile in gate.load_builtin_profiles()])


def _config(profiles: list[dict[str, object]], key: str) -> dict[str, object]:
    return cast("dict[str, object]", next(p["config_json"] for p in profiles if p["profile_key"] == key))


def _permissions(profiles: list[dict[str, object]], key: str) -> dict[str, object]:
    return cast("dict[str, object]", _config(profiles, key)["permissions"])


def test_the_builtin_profiles_conform_to_the_baseline() -> None:
    assert gate.audit_profiles(gate.load_builtin_profiles()) == []


@pytest.mark.parametrize("action", _READONLY_MUST_DENY)
@pytest.mark.parametrize("value", ["allow", "ask", None])
def test_the_read_only_profile_must_deny_each_mutating_action(action: str, value: str | None) -> None:
    profiles = _profiles()
    permissions = _permissions(profiles, "readonly")
    if value is None:
        del permissions[action]
    else:
        permissions[action] = value

    assert gate.audit_profiles(profiles) == [f"Profile 'readonly': '{action}' must be 'deny', got '{value}'"]


@pytest.mark.parametrize("action", _WORKSPACE_MUST_NOT_ALLOW)
def test_the_workspace_profile_must_not_allow_a_sensitive_action_outright(action: str) -> None:
    profiles = _profiles()
    _permissions(profiles, "workspace")[action] = "allow"

    assert gate.audit_profiles(profiles) == [f"Profile 'workspace': '{action}' must not be 'allow'"]


@pytest.mark.parametrize("action", _WORKSPACE_MUST_NOT_ALLOW)
@pytest.mark.parametrize("value", ["ask", "deny"])
def test_the_workspace_profile_may_leave_a_sensitive_action_to_an_approval_or_deny_it(action: str, value: str) -> None:
    profiles = _profiles()
    _permissions(profiles, "workspace")[action] = value

    assert gate.audit_profiles(profiles) == []


@pytest.mark.parametrize("key", ["readonly", "workspace"])
def test_yolo_mode_is_a_violation_in_a_restricted_profile(key: str) -> None:
    profiles = _profiles()
    _config(profiles, key)["yoloModeEnabled"] = True

    assert gate.audit_profiles(profiles) == [f"Profile '{key}': yoloModeEnabled must be False"]


@pytest.mark.parametrize("key", ["readonly", "workspace", "full_access"])
def test_every_profile_lists_its_capabilities_explicitly(key: str) -> None:
    profiles = _profiles()
    del _config(profiles, key)["capabilities"]

    assert gate.audit_profiles(profiles) == [f"Profile '{key}': capabilities must be an explicit list"]


def test_a_profile_without_a_config_is_a_violation() -> None:
    profiles = _profiles()
    profiles[0]["config_json"] = None

    assert gate.audit_profiles(profiles) == [f"Profile '{profiles[0]['profile_key']}': missing or invalid config_json"]


def test_a_read_only_profile_with_malformed_permissions_is_a_violation() -> None:
    profiles = _profiles()
    _config(profiles, "readonly")["permissions"] = ["shell_exec"]

    assert gate.audit_profiles(profiles) == ["Profile 'readonly': permissions must be a dict"]


def test_an_audit_with_no_profile_to_read_fails_instead_of_passing() -> None:
    assert gate.audit_profiles([]) != []


def test_the_script_exits_with_failure_and_names_the_violation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    profiles = _profiles()
    _config(profiles, "readonly")["yoloModeEnabled"] = True
    # The script runs afresh below, so the profiles it audits are swapped where it reads them.
    monkeypatch.setattr("app.services.security.profile_manager._BUILTIN_PROFILES", profiles)

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(_GATE), run_name="__main__")

    assert raised.value.code == 1
    assert "Profile 'readonly': yoloModeEnabled must be False" in capsys.readouterr().out


def test_the_script_runs_standalone_from_any_directory(tmp_path: Path) -> None:
    # Called by path the way run_architecture_gates.sh does: its own interpreter path starts at scripts/ci, and the
    # server is not an installed package, so the import of `app` depends on the script itself, not on the working
    # directory or a PYTHONPATH.
    env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    done = subprocess.run(
        [sys.executable, str(_GATE)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert "PASSED" in done.stdout
