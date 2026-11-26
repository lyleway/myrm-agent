"""Chrome E2E: the Security Profile selector in Settings > Security.

What the user does: open Settings > Security, read which profile is active on the selector button, open it, pick a
profile. The button then names the picked profile and the server reports it as the one active profile. The page also
applies the profile's policy: the YOLO switch follows it, the saved security config carries it, and the agent's shell
approval check decides the way the profile promises (Read Only denies, Workspace asks, Full Access runs without asking).
Picking a profile that was deleted in the meantime tells the user it failed and leaves the stack as it was.
Every UI reading is anchored by the server's own listing. A stack that has no active profile yet (a fresh database lists
the builtin profiles with none activated) gets one activated first; the active profile and the security config the test
found are put back at the end.

Formal run::

    MYRM_E2E_ISOLATED_RUNTIME_ID=<id> ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_security_profile_selector_chrome_e2e.py
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cdp_chat.support import fetch_config_value, put_config_value  # noqa: E402

from tests.support.chrome_mcp_e2e import (  # noqa: E402
    ChromeMcpClient,
    McpPage,
    _warm_ui_parallel_wait_sec,
    dismiss_blocking_modals,
    get_e2e_api_url,
    http_json,
    http_json_once,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)

_PROFILES_PATH = "/api/v1/security/profiles"
_PROBE_PATH = "/api/v1/security/allowlist/test/hitl-probe"
_SECURITY_CONFIG = "securityConfig"
_TRIGGER = '[data-testid="security-profile-trigger"]'
_YOLO_SWITCH = '[data-testid="security-yolo-switch"]'

# What each builtin profile's description promises, read off the probe's shell command (curl through
# bash_code_execute_tool): Read Only has no shell, Workspace asks before one, Full Access runs in YOLO mode.
_PROMISES: dict[str, dict[str, object]] = {
    "readonly": {"yolo_active": False, "action": "deny"},
    "full_access": {"yolo_active": True, "expects_ask": False},
    "workspace": {"yolo_active": False, "action": "ask"},
}
_POLICY_KEYS = ("yolo_active", "auto_mode", "action", "expects_ask", "permissions")

# Radix opens a menu on pointerdown and fires an item on the full pointer sequence, so a bare .click() does neither.
_PRESS_JS = """((selector) => {
  const target = document.querySelector(selector);
  if (!target) return { ok: false, err: 'missing', selector };
  const opts = { bubbles: true, cancelable: true, composed: true, button: 0 };
  target.dispatchEvent(new PointerEvent('pointerdown', opts));
  target.dispatchEvent(new MouseEvent('mousedown', opts));
  target.dispatchEvent(new PointerEvent('pointerup', opts));
  target.dispatchEvent(new MouseEvent('mouseup', opts));
  target.dispatchEvent(new MouseEvent('click', opts));
  return { ok: true, selector };
})"""

_PRESENT_JS = """((selector) => ({ ready: !!document.querySelector(selector), selector }))"""

_FAILURE_TOAST_JS = """(() => ({
  ready: /Failed to activate profile|启用配置集失败/.test(document.body?.innerText || ''),
}))()"""


def _call(function_js: str, selector: str) -> str:
    return f"({function_js})({json.dumps(selector)})"


def _label_js(expected: str | None = None) -> str:
    """The selector button's text; ready once the button exists and, when *expected* is given, reads exactly that."""
    return f"""(() => {{
  const trigger = document.querySelector({json.dumps(_TRIGGER)});
  const label = (trigger?.textContent || '').trim();
  const expected = {json.dumps(expected)};
  return {{ ready: !!trigger && (expected === null || label === expected), label }};
}})()"""


def _yolo_js(expected: bool) -> str:
    """The YOLO switch's state; ready once the switch exists and reads *expected*."""
    return f"""(() => {{
  const state = document.querySelector({json.dumps(_YOLO_SWITCH)})?.getAttribute('aria-checked') ?? null;
  return {{ ready: state === {json.dumps(str(expected).lower())}, state }};
}})()"""


def _listing(api_url: str) -> dict[str, object]:
    listing = http_json("GET", f"{api_url}{_PROFILES_PATH}")
    assert isinstance(listing, dict), listing
    return listing


def _profiles(listing: dict[str, object]) -> list[dict[str, object]]:
    profiles = listing.get("profiles")
    assert isinstance(profiles, list), listing
    return [profile for profile in profiles if isinstance(profile, dict)]


def _display_names(listing: dict[str, object]) -> dict[str, str]:
    return {str(profile["profileKey"]): str(profile["displayName"]) for profile in _profiles(listing)}


def _profile_configs(listing: dict[str, object]) -> dict[str, dict[str, object]]:
    configs: dict[str, dict[str, object]] = {}
    for profile in _profiles(listing):
        config = profile.get("configJson")
        assert isinstance(config, dict), profile
        configs[str(profile["profileKey"])] = config
    return configs


def _active_profile_keys(listing: dict[str, object]) -> list[str]:
    return [str(profile["profileKey"]) for profile in _profiles(listing) if profile.get("isActive")]


def _activate(api_url: str, profile_key: str) -> None:
    http_json("POST", f"{api_url}{_PROFILES_PATH}/{profile_key}/activate")


def _wait_for_active_key(api_url: str, expected: str) -> dict[str, object]:
    deadline = time.monotonic() + 30.0
    listing = _listing(api_url)
    while listing.get("activeKey") != expected and time.monotonic() < deadline:
        time.sleep(1.0)
        listing = _listing(api_url)
    return listing


def _probe(api_url: str) -> dict[str, object]:
    """The agent's shell approval check under the security config the server holds right now."""
    probe = http_json("GET", f"{api_url}{_PROBE_PATH}")
    assert isinstance(probe, dict), probe
    return probe


def _wait_for_policy(api_url: str, expected: dict[str, object]) -> dict[str, object]:
    """The probe once it reads *expected*, or as it stands after 30s (the page saves the config a beat after the pick)."""
    deadline = time.monotonic() + 30.0
    probe = _probe(api_url)
    while any(probe.get(key) != value for key, value in expected.items()) and time.monotonic() < deadline:
        time.sleep(1.0)
        probe = _probe(api_url)
    return probe


@contextlib.contextmanager
def _restoring_security_state(api_url: str) -> Iterator[None]:
    """Picking a profile rewrites the global security config; hand the stack back as it was found."""
    active = _listing(api_url).get("activeKey")
    config = fetch_config_value(_SECURITY_CONFIG, api_url=api_url)
    try:
        yield
    finally:
        if isinstance(active, str):
            _activate(api_url, active)
        put_config_value(_SECURITY_CONFIG, config, api_url=api_url)


def _open_security_settings() -> contextlib.AbstractContextManager[tuple[ChromeMcpClient, McpPage]]:
    warm_ui_route("/settings")
    warm_ui_route("/settings/security", timeout_sec=_warm_ui_parallel_wait_sec(180.0))
    return open_settings_subroute("/settings/security", timeout_ms=120_000)


def _pick(client: ChromeMcpClient, page: McpPage, profile_key: str) -> None:
    """Open the selector and choose *profile_key* the way a pointer does."""
    opened = client.evaluate(page, _call(_PRESS_JS, _TRIGGER), timeout_sec=15.0)
    assert isinstance(opened, dict) and opened.get("ok") is True, opened
    option = f'[data-testid="security-profile-option-{profile_key}"]'
    wait_for_state(client, page, _call(_PRESENT_JS, option), timeout_sec=_warm_ui_parallel_wait_sec(30.0))
    picked = client.evaluate(page, _call(_PRESS_JS, option), timeout_sec=15.0)
    assert isinstance(picked, dict) and picked.get("ok") is True, picked


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_security_profile_selector_shows_the_active_profile_and_activates_another() -> None:
    api_url = get_e2e_api_url()
    prepare_e2e_ui_session(api_url)

    before = _listing(api_url)
    names = _display_names(before)
    assert len(names) >= 2, before
    active = before.get("activeKey")
    original = active if isinstance(active, str) and active in names else None
    start = original or next(iter(names))
    if original is None:
        _activate(api_url, start)
    target = next(key for key in names if key != start)

    with _restoring_security_state(api_url), _open_security_settings() as (client, page):
        dismiss_blocking_modals(client, page)

        shown = wait_for_state(client, page, _label_js(), timeout_sec=_warm_ui_parallel_wait_sec(90.0))
        assert shown["label"] == names[start], (
            f"the selector must name the server's active profile {names[start]!r}, it shows {shown['label']!r}"
        )

        _pick(client, page, target)

        after = _wait_for_active_key(api_url, target)
        assert after.get("activeKey") == target, f"the server never activated {target!r}: {after.get('activeKey')!r}"
        assert _active_profile_keys(after) == [target], _active_profile_keys(after)

        renamed = wait_for_state(client, page, _label_js(names[target]), timeout_sec=_warm_ui_parallel_wait_sec(30.0))
        print(f"E2E_PROFILE_SELECTOR: {names[start]!r} -> {renamed['label']!r} (server activeKey={target})", flush=True)


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_picking_a_profile_applies_its_policy_to_the_agent() -> None:
    api_url = get_e2e_api_url()
    prepare_e2e_ui_session(api_url)
    names = _display_names(_listing(api_url))
    assert set(_PROMISES) <= set(names), names

    with _restoring_security_state(api_url), _open_security_settings() as (client, page):
        dismiss_blocking_modals(client, page)
        wait_for_state(client, page, _label_js(), timeout_sec=_warm_ui_parallel_wait_sec(90.0))

        # Each pick differs from the one before in what the agent decides, so a pick that applies nothing shows.
        for key, promise in _PROMISES.items():
            _pick(client, page, key)
            wait_for_state(client, page, _label_js(names[key]), timeout_sec=_warm_ui_parallel_wait_sec(30.0))
            wait_for_state(client, page, _yolo_js(bool(promise["yolo_active"])), timeout_sec=_warm_ui_parallel_wait_sec(30.0))

            probe = _wait_for_policy(api_url, promise)
            decided = {name: probe.get(name) for name in promise}
            assert decided == promise, (
                f"{names[key]!r} must make the agent decide {promise}, the server holds {probe} "
                f"in the saved config {fetch_config_value(_SECURITY_CONFIG, api_url=api_url)}"
            )
            print(f"E2E_PROFILE_POLICY: {key} -> {decided}", flush=True)


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_activating_a_profile_deleted_meanwhile_reports_the_failure_and_changes_nothing() -> None:
    api_url = get_e2e_api_url()
    prepare_e2e_ui_session(api_url)
    stale_key = f"e2e-stale-{uuid.uuid4().hex[:8]}"
    listing = _listing(api_url)
    configs = _profile_configs(listing)
    assert {"readonly", "full_access"} <= set(configs), configs
    readonly_name = _display_names(listing)["readonly"]

    with _restoring_security_state(api_url):
        # Baseline: Read Only is active and applied. The stale profile is a Full Access copy, so applying it would show.
        _activate(api_url, "readonly")
        put_config_value(_SECURITY_CONFIG, configs["readonly"], api_url=api_url)
        http_json(
            "POST",
            f"{api_url}{_PROFILES_PATH}/clone",
            body={"sourceKey": "full_access", "newKey": stale_key, "newDisplayName": f"E2E Stale {stale_key[-8:]}"},
        )
        try:
            _wait_for_policy(api_url, _PROMISES["readonly"])
            with _open_security_settings() as (client, page):
                dismiss_blocking_modals(client, page)
                wait_for_state(client, page, _label_js(readonly_name), timeout_sec=_warm_ui_parallel_wait_sec(90.0))

                # The open page still lists the profile; the user picks it after it was deleted elsewhere.
                http_json("DELETE", f"{api_url}{_PROFILES_PATH}/{stale_key}")
                status, _ = http_json_once("GET", f"{api_url}{_PROFILES_PATH}/{stale_key}")
                assert status == 404, f"the stale profile must be gone before it is picked, got {status}"
                held_policy = {name: _probe(api_url).get(name) for name in _POLICY_KEYS}
                held_config = fetch_config_value(_SECURITY_CONFIG, api_url=api_url)

                _pick(client, page, stale_key)

                wait_for_state(client, page, _FAILURE_TOAST_JS, timeout_sec=_warm_ui_parallel_wait_sec(30.0))
                wait_for_state(client, page, _label_js(readonly_name), timeout_sec=_warm_ui_parallel_wait_sec(15.0))
                wait_for_state(client, page, _yolo_js(False), timeout_sec=_warm_ui_parallel_wait_sec(15.0))

            assert _listing(api_url).get("activeKey") == "readonly", "the failed pick moved the active profile"
            assert fetch_config_value(_SECURITY_CONFIG, api_url=api_url) == held_config, "the failed pick rewrote the config"
            assert {name: _probe(api_url).get(name) for name in _POLICY_KEYS} == held_policy
            print(f"E2E_PROFILE_STALE: failure toast shown, still {readonly_name!r} (policy={held_policy['action']})", flush=True)
        finally:
            http_json_once("DELETE", f"{api_url}{_PROFILES_PATH}/{stale_key}")
