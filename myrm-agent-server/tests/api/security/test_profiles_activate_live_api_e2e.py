"""Live-stack API E2E: the server calls behind the Settings > Security profile selector.

Task flows (no browser, no model):

- The server's profile list seeds the builtin profiles, one of them is activated, and an activation request for an
  unknown key is refused with 404. The profile that was active stays active: the list still names it, and it is still
  the only active profile.
- A user picks a profile while a page loads the profile list. Both calls succeed (the list read never makes the pick
  fail with "database is busy") and the picked profile ends up the only active one.

Run::

    ./myrm-dev test -m e2e myrm-agent/myrm-agent-server/tests/api/security/test_profiles_activate_live_api_e2e.py
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from tests.api.agent.skill_manage_api_helpers import isolated_backend_url

ACTIVE_KEY = "workspace"
PICKS = ("readonly", "workspace", "full_access")
PICK_ROUNDS = 20


def _active_keys(listing: dict[str, object]) -> list[str]:
    profiles = listing["profiles"]
    assert isinstance(profiles, list), listing
    return [str(profile["profileKey"]) for profile in profiles if isinstance(profile, dict) and profile["isActive"]]


@pytest.mark.e2e
@pytest.mark.timeout(120)
def test_unknown_profile_activation_is_refused_and_keeps_the_active_profile() -> None:
    profiles_url = f"{isolated_backend_url()}/api/v1/security/profiles"

    with httpx.Client(timeout=30.0) as client:
        seeded = client.get(profiles_url)
        assert seeded.status_code == 200, seeded.text
        assert ACTIVE_KEY in {profile["profileKey"] for profile in seeded.json()["profiles"]}, seeded.text

        activated = client.post(f"{profiles_url}/{ACTIVE_KEY}/activate")
        assert activated.status_code == 200, activated.text
        assert activated.json()["isActive"] is True, activated.text

        refused = client.post(f"{profiles_url}/no-such-profile-{uuid.uuid4().hex[:8]}/activate")
        assert refused.status_code == 404, refused.text

        listing = client.get(profiles_url).json()
        assert listing["activeKey"] == ACTIVE_KEY, f"the refused activation dropped the active profile: {listing['activeKey']!r}"
        assert _active_keys(listing) == [ACTIVE_KEY], listing


def _pick_while_listing(client: httpx.Client, profiles_url: str, profile_key: str) -> tuple[httpx.Response, httpx.Response]:
    """The user's pick and a page's list read, started at the same moment."""
    both_ready = threading.Barrier(2)

    def send(method: str, url: str) -> httpx.Response:
        both_ready.wait(timeout=10.0)
        return client.request(method, url)

    with ThreadPoolExecutor(max_workers=2) as pool:
        pick = pool.submit(send, "POST", f"{profiles_url}/{profile_key}/activate")
        listing = pool.submit(send, "GET", profiles_url)
        return pick.result(), listing.result()


@pytest.mark.e2e
@pytest.mark.timeout(180)
def test_picking_a_profile_succeeds_while_the_profile_list_is_read() -> None:
    profiles_url = f"{isolated_backend_url()}/api/v1/security/profiles"

    with httpx.Client(timeout=30.0) as client:
        assert client.get(profiles_url).status_code == 200

        picks = [PICKS[round_index % len(PICKS)] for round_index in range(PICK_ROUNDS)]
        for picked_key in picks:
            pick, listing = _pick_while_listing(client, profiles_url, picked_key)
            assert (pick.status_code, listing.status_code) == (200, 200), (picked_key, pick.text, listing.text)

        final = client.get(profiles_url).json()
        assert final["activeKey"] == picks[-1], final
        assert _active_keys(final) == [picks[-1]], final
