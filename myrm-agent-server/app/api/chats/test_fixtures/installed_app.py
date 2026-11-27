"""Local-only installed-app Chrome E2E seed route.

[INPUT]
app.config.deploy_mode::is_local_mode (POS: local/tauri gate)
app.core.artifacts::LocalArtifactProcessor (POS: registers workspace files as artifacts, as a chat run does)
app.services.agent.params.workspace_resolve::resolve_default_chat_workspace_dir (POS: the chat's sandbox workspace, where a run writes its files)
app.services.agent.agent_service::AgentService (POS: agent list, first agent bound to the seed chat)
app.services.chat.chat_service::ChatService (POS: chat persistence)
myrm_agent_harness.core.context_vars::workspace_root_var (POS: root bound to the active run)

[OUTPUT]
seed_installed_app_fixture: a chat whose assistant answer carries one HTML artifact that declares the notify and fetch bridge capabilities
revise_installed_app_fixture: saves a new version of that artifact that declares other fetch hosts and, on request, the ai capability, as an agent editing the page would

[POS]
Mounted via test_fixtures/__init__.py router include. The artifact is registered by the product's own artifact processor
and shown on the answer the way a run's output is, so the install button of its card is the one a user presses.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from myrm_agent_harness.core.context_vars import workspace_root_var
from pydantic import BaseModel

from app.config.deploy_mode import is_local_mode
from app.core.artifacts import LocalArtifactProcessor
from app.database.dto import ChatCreate
from app.services.agent.agent_service import AgentService
from app.services.agent.params.workspace_resolve import resolve_default_chat_workspace_dir
from app.services.chat.chat_service import ChatService

router = APIRouter()

_APP_NAME = "pulse.html"
_DECLARED_HOST = "example.com"

# A page cannot be clicked from outside its sandbox, so the host posts `e2e-run`, which stands for the user's click:
# one round of calls (a notification, the declared host, a host outside the declaration) whose outcomes the page reports
# back. `e2e-burst` fires `count` notifications at once. `e2e-ask` puts one question to the user's model and reports
# whether an answer came, not its text. `e2e-ping` is answered once the page's script is running.
_APP_PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Pulse</title>
<script type="application/json" id="myrm-app">__DECLARATION__</script></head>
<body><h1 id="out">idle</h1>
<script>
async function attempt(label, call) {
  try {
    await call();
    return label + ':ok';
  } catch (error) {
    return label + ':' + ((error && error.code) || 'failed');
  }
}
window.addEventListener('message', async (event) => {
  if (event.data && event.data.type === 'e2e-ping') {
    window.parent.postMessage({ type: 'e2e-pong' }, '*');
    return;
  }
  if (event.data && event.data.type === 'e2e-burst') {
    const outcomes = await Promise.all(Array.from({ length: event.data.count }, () => attempt(
      'notify', () => window.myrm.notify({ title: 'Pulse burst', body: 'all at once' }),
    )));
    window.parent.postMessage({ type: 'e2e-result', round: event.data.round, outcomes }, '*');
    return;
  }
  if (event.data && event.data.type === 'e2e-ask') {
    const asked = [await attempt('ai', async () => {
      const text = await window.myrm.llm.complete({ prompt: 'Reply with the single word: pong', maxTokens: 32 });
      if (!text.trim()) throw { code: 'empty' };
    })];
    document.getElementById('out').textContent = asked.join(' ');
    window.parent.postMessage({ type: 'e2e-result', round: event.data.round, outcomes: asked }, '*');
    return;
  }
  if (!event.data || event.data.type !== 'e2e-run') return;
  const outcomes = [];
  outcomes.push(await attempt('notify', () => window.myrm.notify({ title: 'Pulse ping', body: 'hello from the app' })));
  outcomes.push(await attempt('fetch-declared', async () => {
    const response = await window.myrm.fetch('https://example.com/');
    if (response.status !== 200) throw { code: 'status-' + response.status };
  }));
  outcomes.push(await attempt('fetch-undeclared', () => window.myrm.fetch('https://example.org/')));
  document.getElementById('out').textContent = outcomes.join(' ');
  window.parent.postMessage({ type: 'e2e-result', round: event.data.round, outcomes }, '*');
});
</script></body></html>
"""


def _declaration(fetch_hosts: list[str], ai: bool) -> str:
    return json.dumps({"notify": True, "fetch": fetch_hosts, "ai": ai})


async def _register_page(chat_id: str, workspace: str, fetch_hosts: list[str], ai: bool) -> dict[str, object]:
    """Write the page into the chat's workspace and register it as the product's artifact processor would."""
    folder = Path(workspace) / Path(_APP_NAME).stem
    folder.mkdir(parents=True, exist_ok=True)
    page = folder / _APP_NAME
    page.write_text(_APP_PAGE_TEMPLATE.replace("__DECLARATION__", _declaration(fetch_hosts, ai)), encoding="utf-8")

    event: dict[str, object] = {"type": "artifacts_ready", "data": [{"filename": _APP_NAME, "path": str(page)}]}
    bound_root = workspace_root_var.set(workspace)
    try:
        emitted = await LocalArtifactProcessor(chat_id=chat_id).process_artifacts_ready(event)
    finally:
        workspace_root_var.reset(bound_root)
    rows = cast(list[dict[str, object]], emitted["data"]) if emitted else []
    if len(rows) != 1:
        raise HTTPException(status_code=500, detail="Installed-app E2E seed could not register its page")
    return rows[0]


@router.post("/test/seed-installed-app-fixture", include_in_schema=False)
async def seed_installed_app_fixture() -> dict[str, str]:
    """Local dev/test only: a fresh chat whose assistant answer holds an installable HTML app artifact."""
    if not is_local_mode():
        raise HTTPException(status_code=404, detail="Not found")

    agents, _total = await AgentService.get_agent_list(1, 100)
    if not agents:
        raise HTTPException(status_code=500, detail="No agents available for installed-app E2E seed")

    chat_id = f"e2eapp{uuid4().hex[:8]}"
    await ChatService.create_or_update_chat(
        ChatCreate(chat_id=chat_id, title="Installed app Chrome E2E", agent_id=agents[0].id, messages=[]),
    )
    workspace = await resolve_default_chat_workspace_dir(chat_id, persist_workspace=True)
    if not workspace:
        raise HTTPException(status_code=500, detail="Failed to resolve workspace for installed-app E2E seed")

    row = await _register_page(chat_id, workspace, [_DECLARED_HOST], ai=False)

    now = datetime.now(UTC)
    await ChatService.append_message(chat_id, "user", "Make me a small app that pings me.", now, "UTC")
    await ChatService.append_message(
        chat_id,
        "assistant",
        "Here is the app.",
        now,
        "UTC",
        extra_data={"artifacts": [row]},
    )
    return {"chat_id": chat_id, "artifact_id": str(row["id"]), "filename": _APP_NAME}


class ReviseFixtureBody(BaseModel):
    chat_id: str
    fetch_hosts: list[str]
    ai: bool = False


@router.post("/test/seed-installed-app-fixture/revise", include_in_schema=False)
async def revise_installed_app_fixture(body: ReviseFixtureBody) -> dict[str, str]:
    """Local dev/test only: save the seeded page again with other declarations, the way an agent edits it."""
    if not is_local_mode():
        raise HTTPException(status_code=404, detail="Not found")
    workspace = await resolve_default_chat_workspace_dir(body.chat_id, persist_workspace=True)
    if not workspace:
        raise HTTPException(status_code=404, detail="Chat has no workspace")
    row = await _register_page(body.chat_id, workspace, body.fetch_hosts, body.ai)
    return {"artifact_id": str(row["id"])}
