"""A login put in a home, for the tests that need one to exist.

[OUTPUT]
- plant_login: Write the credential file a CLI keeps its login in, in the home an environment reaches.

[POS]
Test support: the CLI makes a login in real life, and the host never writes one, so a test makes it by hand.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from myrm_agent_harness.toolkits.acp.drivers import driver_for


def plant_login(env: Mapping[str, str], backend: str, content: str = '{"token": "abc"}') -> Path:
    """Write ``content`` as the credential file ``backend`` keeps its login in, in the home ``env`` reaches."""
    profile = driver_for(backend)
    assert profile is not None, backend
    path = profile.resolve_credential_paths(env)[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
