"""The local skill preview answers for a directory whose SKILL.md holds text the response must be able to encode.

[INPUT]
- app.api.skills.local::router (POS: the preview and adopt endpoints of local skill paths)
- app.core.skills.providers.local::LocalSkillsProvider (POS: the provider whose ``preview_path`` the endpoints call)

[OUTPUT]
- Fails when a lone surrogate spelled as an escape in a frontmatter turns the preview into a server error, or when the preview
  reports a failure with text that is not meant for the user.

[POS]
A local skill directory is text the user did not write. The preview parses its frontmatter twice (once for the skill, once for
the raw fields it echoes), and both parses must leave text the JSON response can encode.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.skills.local import router as local_router

REPLACED = "\ufffd"
# The escapes are the YAML text a skill author (or an attacker) puts in the file; they decode to lone surrogates.
SKILL_MD = (
    '---\nname: "spoof\\ud800"\ndescription: "a\\ud800b"\nauthor: "au\\udc80"\ntags:\n  - "t\\udfff"\n---\n# Instructions\n'
)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI(title="Local skills preview test app")
    app.include_router(local_router, prefix="/api/v1/skills", tags=["skills"])
    return TestClient(app)


def test_the_preview_of_a_frontmatter_spelling_lone_surrogates_is_a_normal_answer(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

    with (
        patch("app.api.skills.local.require_local_skills_capability"),
        patch("app.api.skills.local.skills_service.list_skills", new_callable=AsyncMock, return_value=[]),
    ):
        response = client.post("/api/v1/skills/local/paths/preview", json={"path": str(tmp_path)})

    assert response.status_code == 200
    skill = response.json()["skills"][0]
    assert (skill["name"], skill["description"], skill["author"]) == (
        f"spoof{REPLACED}",
        f"a{REPLACED}b",
        f"au{REPLACED}",
    )
    assert skill["tags"] == [f"t{REPLACED}"]
