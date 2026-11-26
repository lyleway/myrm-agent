"""The chat context endpoints that sit beside compact-chat: summary override, archive, pins and snapshot branches.

Each answers 404 for a chat that does not exist, before it reads or writes anything of the chat, and answers with what
it read or wrote for one that does. The chat store is stood in; pins and branches are real files under a private
persistent root.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from myrm_agent_harness.runtime.paths.execution_paths import PERSISTENT_ROOT_ENV

from app.api.chats.chat.compaction import router
from app.database.connection import get_db
from app.schemas.responses import BusinessCode
from app.services.chat.chat_service import ChatService
from app.services.chat.context_branch_fork import BranchForkResult, ContextBranchForkService

_CHAT = "chat-1"
_ENDPOINTS = [
    ("PUT", "/compaction/summary", {"summary": "{}"}),
    ("GET", "/archive", None),
    ("GET", "/context/pins", None),
    ("PUT", "/context/pins", {"files": ["src/a.py"]}),
    ("POST", "/context/pins", {"file_path": "src/a.py"}),
    ("GET", "/context/branches", None),
    ("POST", "/context/branches", {"snapshot_path": ".context/snap.jsonl"}),
    ("POST", "/context/branches/branch-1/fork", None),
]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(PERSISTENT_ROOT_ENV, str(tmp_path))
    app = FastAPI()
    app.include_router(router)

    async def _no_db() -> None:
        return None

    app.dependency_overrides[get_db] = _no_db
    with TestClient(app) as test_client:
        yield test_client


def _chat_exists():
    return patch.object(ChatService, "get_chat_metadata", new=AsyncMock(return_value=SimpleNamespace(id=_CHAT)))


def _chat_is_missing():
    return patch.object(ChatService, "get_chat_metadata", new=AsyncMock(return_value=None))


@pytest.mark.parametrize(("method", "path", "body"), _ENDPOINTS, ids=[f"{method} {path}" for method, path, _ in _ENDPOINTS])
def test_a_chat_that_does_not_exist_is_a_404(client: TestClient, method: str, path: str, body: dict[str, object] | None) -> None:
    with _chat_is_missing():
        response = client.request(method, f"/{_CHAT}{path}", json=body)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == BusinessCode.RESOURCE_NOT_FOUND.value


class TestSummaryOverride:
    def test_the_summary_is_stored_for_the_chat(self, client: TestClient) -> None:
        store = AsyncMock()

        with _chat_exists(), patch.object(ChatService, "update_compaction_summary", new=store):
            response = client.put(f"/{_CHAT}/compaction/summary", json={"summary": '{"user_goal":"ship"}'})

        assert response.status_code == 200
        store.assert_awaited_once_with(_CHAT, '{"user_goal":"ship"}')

    def test_a_summary_that_cannot_be_stored_is_a_500(self, client: TestClient) -> None:
        store = AsyncMock(side_effect=RuntimeError("disk full"))

        with _chat_exists(), patch.object(ChatService, "update_compaction_summary", new=store):
            response = client.put(f"/{_CHAT}/compaction/summary", json={"summary": "{}"})

        assert response.status_code == 500
        assert response.json()["detail"]["message"].startswith("Update compaction summary failed")


class TestArchive:
    def test_the_messages_the_compaction_set_aside_come_back(self, client: TestClient) -> None:
        archived = [{"role": "user", "content": "hello"}]

        with (
            _chat_exists(),
            patch("app.services.chat.compact_service.get_archived_messages", new=AsyncMock(return_value=archived)),
        ):
            response = client.get(f"/{_CHAT}/archive")

        assert response.status_code == 200
        assert response.json()["data"] == {"messages": archived}

    def test_an_archive_that_cannot_be_read_is_a_500(self, client: TestClient) -> None:
        unreadable = AsyncMock(side_effect=OSError("backup is unreadable"))

        with _chat_exists(), patch("app.services.chat.compact_service.get_archived_messages", new=unreadable):
            response = client.get(f"/{_CHAT}/archive")

        assert response.status_code == 500
        assert response.json()["detail"]["message"].startswith("Get chat archive failed")


class TestPins:
    def test_a_pinned_file_joins_the_pins_the_chat_already_has(self, client: TestClient) -> None:
        with _chat_exists():
            client.put(f"/{_CHAT}/context/pins", json={"files": ["src/a.py"]})
            response = client.post(f"/{_CHAT}/context/pins", json={"file_path": "src/b.py"})
            listed = client.get(f"/{_CHAT}/context/pins")

        assert response.status_code == 200
        assert response.json()["data"]["files"] == ["src/a.py", "src/b.py"]
        assert listed.json()["data"]["files"] == ["src/a.py", "src/b.py"]


class TestBranchFork:
    def test_a_fork_returns_the_chat_it_opened_and_cuts_the_title_to_what_was_typed(self, client: TestClient) -> None:
        fork = AsyncMock(return_value=BranchForkResult(True, "chat-2", _CHAT, "branch-1", 12))

        with _chat_exists(), patch.object(ContextBranchForkService, "fork_from_branch", new=fork):
            response = client.post(f"/{_CHAT}/context/branches/branch-1/fork", json={"new_title": "  Retry  "})

        assert response.status_code == 200
        assert response.json()["data"] == {
            "new_chat_id": "chat-2",
            "parent_chat_id": _CHAT,
            "branch_id": "branch-1",
            "message_count": 12,
        }
        assert fork.await_args.args[1:] == (_CHAT, "branch-1")
        assert fork.await_args.kwargs == {"new_title": "Retry"}

    def test_a_fork_that_cannot_be_made_is_a_400_with_its_reason(self, client: TestClient) -> None:
        refused = AsyncMock(return_value=BranchForkResult(False, None, _CHAT, "branch-1", 0, "Snapshot file is missing"))

        with _chat_exists(), patch.object(ContextBranchForkService, "fork_from_branch", new=refused):
            response = client.post(f"/{_CHAT}/context/branches/branch-1/fork")

        assert response.status_code == 400
        assert "Snapshot file is missing" in response.json()["detail"]["message"]
