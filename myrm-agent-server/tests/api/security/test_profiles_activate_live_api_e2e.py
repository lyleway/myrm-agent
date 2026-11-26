"""Live-stack API E2E: activating a security profile that does not exist keeps the active profile in place.

Task flow (no browser, no model): the server's profile list seeds the builtin profiles, one of them is activated, and
an activation request for an unknown key is refused with 404. The profile that was active stays active: the list still
names it, and it is still the only active profile.

Run::

    ./myrm-dev test -m e2e myrm-agent/myrm-agent-server/tests/api/security/test_profiles_activate_live_api_e2e.py
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.api.agent.skill_manage_api_helpers import isolated_backend_url

ACTIVE_KEY = "workspace"


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
