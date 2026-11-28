"""Real-model E2E: a generated or edited image reaches the workspace and the chat card, and a masked edit keeps its inputs' size.

[INPUT]
- POST /api/v1/agents/agent-stream with the BASIC model (POS: the whole server path, real provider calls, the real image tool, task queue and worker, media library, workspace exporter and bash tool)
- a loopback image provider (POS: the OpenAI images endpoint the gateway setting points at; it answers with an image of the size it was asked for and records every request)
- GET /api/v1/chats/{id}/messages, /api/v1/tasks/{id}, /api/v1/media/{id}/file (POS: what the chat card of a generated image reads)

[OUTPUT]
- test_generated_image_is_a_file_bash_can_read: the model finds the workspace copy in the tool's answer and bash measures the very bytes the provider sent; the reply carries a card that loads those bytes
- test_masked_edit_inputs_arrive_undownsized_and_the_result_lands_in_the_workspace: source and mask reach the provider at their own size, the edited image lands in the workspace, and its card loads the same bytes

[POS]
Server pytest-only, marker ``e2e`` (``-m e2e``). Proves what the unit tests cannot: that a model uses the path the tool names, and that the
stream carries the result of a media call (the tool announces it under the message id of the turn's steps) so the reply registers a card for it. The provider is the only stand-in, as
the hosting receiver is in test_artifact_publish_by_path_e2e; its images have the size they were asked for, so a wrong size in a request
shows up in the file the model measures.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

import app.lifecycle.task_worker as task_worker_module
from app.api.dependencies import get_db_session
from app.config.settings import get_settings
from app.lifecycle.task_worker import ensure_task_store, start_task_worker, stop_task_worker
from tests.api.agent.utils import check_e2e_errors
from tests.support.media_gateway_stub import MediaGatewayStub, png_bytes, serve_media_gateway
from tests.support.media_library_env import media_library_env
from tests.support.real_model_workspace_turns import harness_workspace, new_chat, perform_agent_stream
from tests.support.test_secrets import resolve_test_env

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.timeout(540, method="thread"),
    pytest.mark.skipif(not resolve_test_env("BASIC_API_KEY"), reason="E2E test requires BASIC_API_KEY environment variable"),
]

_GENERATE_PROMPT = (
    "Generate a small plain red image with the image tool. Once it is finished, use bash to find out the exact size in bytes of "
    "the image file that is in your workspace, taking the path from the tool's answer (do not guess a path), and tell me that number."
)
_EDIT_PROMPT = (
    "Your workspace holds photo.png and mask.png. Use the image tool to edit photo.png with mask.png as the mask and turn the "
    "masked area blue. When it is finished, run the `file` command on the edited image file in your workspace (take its path from "
    "the tool's answer, do not guess a path), quote what `file` printed, and do nothing else."
)
_SOURCE_SIZE = (3000, 2000)  # above the 2048 px that the clamp gives every other image input


@pytest.fixture
def provider() -> Iterator[MediaGatewayStub]:
    with serve_media_gateway() as stub:
        yield stub


@pytest.fixture
def image_world(
    app: FastAPI,
    client: TestClient,
    provider: MediaGatewayStub,
    mock_load_user_configs: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """The user picked an image model and a gateway in the settings; the task worker runs as it does in the server."""
    import app.services.config.encryption as enc_mod

    monkeypatch.setenv("DEPLOY_MODE", "local")
    enc_mod._encryption_service = None
    data_dir = Path(os.environ["MYRM_DATA_DIR"]).resolve()
    assert Path(get_settings().database.state_dir).resolve().is_relative_to(data_dir), "state dir escapes the pytest data dir"

    mock_load_user_configs.return_value.personal_settings_dict = {
        "imageGeneration": {
            "model": "gpt-image-2.6-sunburst",
            "gatewayConfig": {"use_gateway": True, "gateway_url": provider.url, "auth_token": "stub-gateway-token"},
        }
    }
    task_worker_module._task_store_instance = None
    assert client.portal is not None
    with client.portal.wrap_async_context_manager(media_library_env(tmp_path / "media", monkeypatch)) as library:
        # The agent test app mounts no media or task route, and a chat card loads its image through both. Its session
        # factory is a stand-in, so the media route reads the library the tools wrote to.
        async def library_session() -> AsyncIterator[AsyncSession]:
            async with library.sessions() as session:
                yield session

        app.dependency_overrides[get_db_session] = library_session
        app.include_router(import_module("app.api.media").media_router, prefix="/api/v1/media")
        app.include_router(import_module("app.api.tasks.router").router, prefix="/api/v1/tasks")
        client.portal.call(ensure_task_store)
        client.portal.call(start_task_worker)
        try:
            yield
        finally:
            client.portal.call(stop_task_worker)
            task_worker_module._task_store_instance = None
            enc_mod._encryption_service = None


def _agent_and_chat(client: TestClient) -> tuple[str, str]:
    created = client.post("/api/agents", json={"name": "Image workspace tester", "enabled_builtin_tools": ["image_generation"]})
    assert created.status_code == 200, created.text
    return created.json()["data"]["id"], new_chat(client)


def _workspace(client: TestClient, chat_id: str) -> Path:
    assert client.portal is not None
    return client.portal.call(harness_workspace, chat_id)


def _report(collected: list[dict[str, object]], answer: str, provider: MediaGatewayStub) -> None:
    """What a failing run needs to be read: the tool calls the model made, what the provider received, what the model answered."""
    for event in collected:
        if event.get("type") == "tasks_steps" and event.get("tool_name") is not None:
            print(f"CALL {event['tool_name']}: {json.dumps(event.get('data'), ensure_ascii=False)[:400]}", flush=True)
    print("PROVIDER REQUESTS:", json.dumps([asdict(seen) for seen in provider.requests], ensure_ascii=False)[:1500], flush=True)
    print("ANSWER:", answer, flush=True)


def _assert_announced_on_the_turn(collected: list[dict[str, object]]) -> None:
    """The browser folds a ``tool_end`` into the message whose steps carry its ``messageId``; any other id loses the card."""
    announced = {
        event.get("messageId")
        for event in collected
        if event.get("type") == "tool_end" and event.get("tool_name") == "image_tool"
    }
    stepped = {event.get("messageId") for event in collected if event.get("type") == "tasks_steps"}
    assert announced and None not in announced, f"the image tool announced no result to the stream: {announced}"
    assert announced <= stepped, f"the announcement belongs to message {announced}, the turn's steps to {stepped}"


def _card_images(client: TestClient, chat_id: str) -> list[bytes]:
    """The bytes the chat cards of the persisted replies load, each ``media_generated`` entry resolved as the browser does:
    a task entry through the task API, then ``media://<id>`` through the media file route."""
    deadline = time.monotonic() + 15
    while True:
        messages = client.get(f"/api/v1/chats/{chat_id}/messages").json()["data"]["messages"]
        entries = [entry for message in messages for entry in (message.get("metadata") or {}).get("media_generated", [])]
        if entries or time.monotonic() > deadline:
            break
        time.sleep(0.5)  # the reply is persisted when the stream finalizes
    loaded: list[bytes] = []
    for entry in entries:
        if "media" in entry:
            references = [str(entry["media"])]
        else:
            task = client.get(f"/api/v1/tasks/{entry['task_id']}")
            assert task.status_code == 200, f"the card of task {entry['task_id']} cannot read its task: {task.status_code}"
            references = [str(image["url"]) for image in task.json()["result"]["images"]]
        for reference in references:
            response = client.get(f"/api/v1/media/{reference.removeprefix('media://')}/file")
            assert response.status_code == 200, (
                f"the card of {reference} cannot load its file: {response.status_code} {response.text[:200]}"
            )
            loaded.append(response.content)
    return loaded


def test_generated_image_is_a_file_bash_can_read(client: TestClient, image_world: None, provider: MediaGatewayStub) -> None:
    agent_id, chat_id = _agent_and_chat(client)

    answer, collected, _ = perform_agent_stream(client, _GENERATE_PROMPT, chat_id, agent_id=agent_id)

    _report(collected, answer, provider)
    check_e2e_errors(collected)
    _assert_announced_on_the_turn(collected)
    assert provider.responses, "the provider stub was never called: the image tool did not run a generation"
    workspace = _workspace(client, chat_id)
    copies = sorted((workspace / "_generated").glob("*.png"))
    assert len(copies) == 1, f"expected one workspace copy under {workspace}/_generated, found {copies}"
    assert copies[0].read_bytes() == provider.responses[-1]
    assert (workspace / "_generated" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    spoken = re.sub(r"(?<=\d)[,_\s](?=\d{3}\b)", "", answer)  # a model may write 5,333 for 5333
    assert str(len(provider.responses[-1])) in spoken, f"the answer does not carry the file size {len(provider.responses[-1])}"
    assert _card_images(client, chat_id) == [provider.responses[-1]], "the chat card must load the very image the workspace holds"


def test_masked_edit_inputs_arrive_undownsized_and_the_result_lands_in_the_workspace(
    client: TestClient, image_world: None, provider: MediaGatewayStub
) -> None:
    agent_id, chat_id = _agent_and_chat(client)
    workspace = _workspace(client, chat_id)
    (workspace / "photo.png").write_bytes(png_bytes(_SOURCE_SIZE, color=(10, 200, 30)))
    (workspace / "mask.png").write_bytes(png_bytes(_SOURCE_SIZE, mode="RGBA", color=(0, 0, 0, 0)))

    answer, collected, _ = perform_agent_stream(client, _EDIT_PROMPT, chat_id, agent_id=agent_id)

    _report(collected, answer, provider)
    check_e2e_errors(collected)
    _assert_announced_on_the_turn(collected)
    assert provider.edits, "the provider stub got no edit request: the model did not run a masked edit"
    parts = provider.edits[-1].parts
    image_part = next((part for name, part in parts.items() if name.startswith("image")), None)
    assert image_part is not None and "mask" in parts, f"edit parts: {sorted(parts)}"
    assert image_part.pixels == _SOURCE_SIZE, f"the source reached the provider as {image_part.pixels}, not {_SOURCE_SIZE}"
    assert parts["mask"].pixels == _SOURCE_SIZE, f"the mask reached the provider as {parts['mask'].pixels}, not {_SOURCE_SIZE}"
    copies = sorted((workspace / "_generated").glob("*.png"))
    assert len(copies) == 1, f"expected one workspace copy of the edited image under {workspace}/_generated, found {copies}"
    assert copies[0].read_bytes() == provider.responses[-1]
    copy_width, copy_height = Image.open(copies[0]).size
    assert f"{copy_width} x {copy_height}" in answer, f"the answer does not quote what `file` printed: {answer[:300]}"
    assert _card_images(client, chat_id) == [provider.responses[-1]], "the chat card must load the very image the workspace holds"
