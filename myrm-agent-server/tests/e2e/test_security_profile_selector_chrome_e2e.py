"""Chrome E2E: the Security Profile selector in Settings > Security shows the active profile and activates another.

What the user does: open Settings > Security, read which profile is active on the selector button, open it, pick a
different profile. The button then names the picked profile and the server reports it as the one active profile.
Every UI reading is anchored by the server's own listing. A stack that has no active profile yet (a fresh database lists
the builtin profiles with none activated) gets one activated first; an active profile that was there is restored at the end.

Formal run::

    MYRM_E2E_ISOLATED_RUNTIME_ID=<id> ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_security_profile_selector_chrome_e2e.py
"""

from __future__ import annotations

import json
import time

import pytest

from tests.support.chrome_mcp_e2e import (
    _warm_ui_parallel_wait_sec,
    dismiss_blocking_modals,
    get_e2e_api_url,
    http_json,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)

_PROFILES_PATH = "/api/v1/security/profiles"
_TRIGGER = '[data-testid="security-profile-trigger"]'

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


def _listing(api_url: str) -> dict[str, object]:
    listing = http_json("GET", f"{api_url}{_PROFILES_PATH}")
    assert isinstance(listing, dict), listing
    return listing


def _active_profile_keys(listing: dict[str, object]) -> list[str]:
    profiles = listing.get("profiles")
    if not isinstance(profiles, list):
        return []
    return [str(profile["profileKey"]) for profile in profiles if isinstance(profile, dict) and profile.get("isActive")]


def _activate(api_url: str, profile_key: str) -> None:
    http_json("POST", f"{api_url}{_PROFILES_PATH}/{profile_key}/activate")


def _wait_for_active_key(api_url: str, expected: str) -> dict[str, object]:
    deadline = time.monotonic() + 30.0
    listing = _listing(api_url)
    while listing.get("activeKey") != expected and time.monotonic() < deadline:
        time.sleep(1.0)
        listing = _listing(api_url)
    return listing


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
    profiles = before.get("profiles")
    assert isinstance(profiles, list) and len(profiles) >= 2, before
    names = {str(profile["profileKey"]): str(profile["displayName"]) for profile in profiles if isinstance(profile, dict)}
    active = before.get("activeKey")
    original = active if isinstance(active, str) and active in names else None
    start = original or next(iter(names))
    if original is None:
        _activate(api_url, start)
    target = next(key for key in names if key != start)

    warm_ui_route("/settings")
    warm_ui_route("/settings/security", timeout_sec=_warm_ui_parallel_wait_sec(180.0))
    try:
        with open_settings_subroute("/settings/security", timeout_ms=120_000) as (client, page):
            dismiss_blocking_modals(client, page)

            shown = wait_for_state(client, page, _label_js(), timeout_sec=_warm_ui_parallel_wait_sec(90.0))
            assert shown["label"] == names[start], (
                f"the selector must name the server's active profile {names[start]!r}, it shows {shown['label']!r}"
            )

            opened = client.evaluate(page, _call(_PRESS_JS, _TRIGGER), timeout_sec=15.0)
            assert isinstance(opened, dict) and opened.get("ok") is True, opened
            option = f'[data-testid="security-profile-option-{target}"]'
            wait_for_state(client, page, _call(_PRESENT_JS, option), timeout_sec=_warm_ui_parallel_wait_sec(30.0))
            picked = client.evaluate(page, _call(_PRESS_JS, option), timeout_sec=15.0)
            assert isinstance(picked, dict) and picked.get("ok") is True, picked

            after = _wait_for_active_key(api_url, target)
            assert after.get("activeKey") == target, f"the server never activated {target!r}: {after.get('activeKey')!r}"
            assert _active_profile_keys(after) == [target], _active_profile_keys(after)

            renamed = wait_for_state(client, page, _label_js(names[target]), timeout_sec=_warm_ui_parallel_wait_sec(30.0))
            print(f"E2E_PROFILE_SELECTOR: {names[start]!r} -> {renamed['label']!r} (server activeKey={target})", flush=True)
    finally:
        if original is not None:
            _activate(api_url, original)
