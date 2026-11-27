"""Unit tests for Zotero source sync connector."""

from __future__ import annotations

import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from unittest.mock import AsyncMock, patch

import pytest

from app.services.wiki.source_sync import zotero as zotero_module
from app.services.wiki.source_sync.schemas import WikiSourceSyncResult
from app.services.wiki.source_sync.zotero import (
    _format_creators,
    _render_annotations,
    _render_note,
    sync_zotero_library_to_wiki,
)
from tests.support.loopback_origin import LoopbackOrigin, page, redirect


def _note(
    *,
    title: str = "T",
    abstract: str = "",
    annotation_lines: list[str] | None = None,
) -> str:
    return _render_note(
        title=title,
        item_type="journalArticle",
        authors=["A B"],
        doi="10.1/x",
        url="https://x",
        year="2024",
        abstract=abstract,
        annotation_lines=annotation_lines or [],
    )


class TestFormatCreators:
    def test_first_last(self) -> None:
        assert _format_creators([{"firstName": "Jane", "lastName": "Doe"}]) == ["Jane Doe"]

    def test_single_name(self) -> None:
        assert _format_creators([{"name": "ACM"}]) == ["ACM"]

    def test_skips_non_dict(self) -> None:
        assert _format_creators(["bad", 42]) == []


class TestRenderNote:
    def test_full_note(self) -> None:
        text = _note(abstract="Abs", annotation_lines=["> hi\n> — highlight"])
        assert "## Abstract" in text
        assert "## Annotations" in text
        assert "DOI: `10.1/x`" in text

    def test_minimal_note(self) -> None:
        text = _note()
        assert "## Abstract" not in text
        assert "## Annotations" not in text
        assert "DOI: `10.1/x`" in text


class TestRenderAnnotations:
    def test_highlight_and_note(self) -> None:
        children = [
            {
                "data": {
                    "itemType": "annotation",
                    "annotationType": "highlight",
                    "annotationText": "key finding",
                }
            },
            {
                "data": {
                    "itemType": "annotation",
                    "annotationType": "note",
                    "annotationComment": "my thought",
                }
            },
        ]
        lines = _render_annotations(children)
        assert lines[0].startswith("> key finding")
        assert lines[1] == "- my thought"

    def test_skips_non_annotation_and_empty(self) -> None:
        children = [
            {"data": {"itemType": "note", "note": "regular child note"}},
            {
                "data": {
                    "itemType": "annotation",
                    "annotationType": "highlight",
                    "annotationText": "",
                }
            },
        ]
        assert _render_annotations(children) == []


class TestSyncZoteroLibrary:
    @pytest.mark.asyncio
    async def test_missing_config_fails_fast(self) -> None:
        result = await sync_zotero_library_to_wiki(
            object(),  # type: ignore[arg-type]
            api_key="",
            user_id="",
            max_items=5,
            auto_compile=False,
            compiler_enqueue=None,
        )
        assert result.source == "zotero"
        assert result.failed == 1
        assert "not configured" in result.errors[0]

    @pytest.mark.asyncio
    async def test_invalid_base_url_rejected(self) -> None:
        result = await sync_zotero_library_to_wiki(
            object(),  # type: ignore[arg-type]
            api_key="k",
            user_id="u",
            max_items=5,
            auto_compile=False,
            compiler_enqueue=None,
            base_url="ftp://evil",
        )
        assert result.failed == 1
        assert "invalid zotero base url" in result.errors[0]

    @pytest.mark.asyncio
    async def test_publishes_items_and_counts(self) -> None:
        items = [
            {
                "key": "K1",
                "data": {
                    "itemType": "journalArticle",
                    "title": "Paper One",
                    "abstractNote": "A1",
                    "creators": [{"firstName": "A", "lastName": "B"}],
                    "DOI": "10.1/one",
                    "date": "2024",
                },
            },
            {"key": "K2", "data": {"itemType": "attachment", "title": "pdf"}},
        ]
        publish_result = _publish_result(written=True)
        with (
            patch(
                "app.services.wiki.source_sync.zotero._fetch_top_level_items",
                AsyncMock(return_value=items),
            ),
            patch(
                "app.services.wiki.source_sync.zotero._fetch_annotations",
                AsyncMock(return_value=["- n1"]),
            ),
            patch(
                "app.services.wiki.source_sync.zotero.publish_source_markdown",
                AsyncMock(return_value=publish_result),
            ),
        ):
            result = await sync_zotero_library_to_wiki(
                object(),  # type: ignore[arg-type]
                api_key="k",
                user_id="u",
                max_items=10,
                auto_compile=False,
                compiler_enqueue=None,
            )
        assert result.published == 1
        assert result.skipped == 1

    @pytest.mark.asyncio
    async def test_fetch_error_counts_failed(self) -> None:
        with patch(
            "app.services.wiki.source_sync.zotero._fetch_top_level_items",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await sync_zotero_library_to_wiki(
                object(),  # type: ignore[arg-type]
                api_key="k",
                user_id="u",
                max_items=5,
                auto_compile=False,
                compiler_enqueue=None,
            )
        assert result.failed == 1
        assert "boom" in result.errors[0]


class TestConfiguredAddress:
    """The library address is the user's own setting: it is fetched like any outbound request, over real sockets."""

    _ITEMS_PATH = "/api/users/u/items/top?format=json&limit=10&sort=dateModified&direction=desc"
    _UNREACHABLE = "the address could not be reached"

    @pytest.fixture(autouse=True)
    def _public_addresses_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MYRM_ENABLE_SSRF_SHIELD", "true")
        monkeypatch.delenv("MYRM_ALLOWED_INTERNAL_HOSTS", raising=False)

    @pytest.fixture
    def origin(self) -> Iterator[LoopbackOrigin]:
        server = LoopbackOrigin()
        yield server
        server.close()

    @staticmethod
    async def _sync(base_url: str) -> tuple[WikiSourceSyncResult, AsyncMock]:
        publish = AsyncMock(return_value=_publish_result(written=True))
        with patch("app.services.wiki.source_sync.zotero.publish_source_markdown", publish):
            result = await sync_zotero_library_to_wiki(
                object(),  # type: ignore[arg-type]
                api_key="secret-key",
                user_id="u",
                max_items=10,
                auto_compile=False,
                compiler_enqueue=None,
                base_url=base_url,
            )
        return result, publish

    @pytest.mark.asyncio
    async def test_an_internal_library_address_is_refused_and_never_requested(self, origin: LoopbackOrigin) -> None:
        result, publish = await self._sync(origin.url("/api"))

        assert (result.failed, result.published) == (1, 0)
        assert result.errors == [self._UNREACHABLE]
        assert origin.hits == []
        publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_library_of_a_host_the_operator_allows_is_pulled_with_the_api_key(
        self, origin: LoopbackOrigin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")
        keys_sent: list[str | None] = []
        items = [{"key": "K1", "data": {"itemType": "journalArticle", "title": "Paper One"}}]
        listing = page(json.dumps(items), "application/json")

        def serve_items(handler: BaseHTTPRequestHandler) -> None:
            keys_sent.append(handler.headers.get("Zotero-API-Key"))
            listing(handler)

        origin.routes[self._ITEMS_PATH] = serve_items

        result, publish = await self._sync(origin.url("/api"))

        assert (result.published, result.failed) == (1, 0)
        assert keys_sent == ["secret-key"]
        assert origin.hits[0] == self._ITEMS_PATH
        publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_redirect_is_not_followed_so_the_api_key_goes_nowhere_else(
        self, origin: LoopbackOrigin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")
        origin.routes[self._ITEMS_PATH] = redirect(origin.url("/elsewhere"))
        origin.routes["/elsewhere"] = page("[]", "application/json")

        result, _ = await self._sync(origin.url("/api"))

        assert result.errors == [self._UNREACHABLE]
        assert origin.hits == [self._ITEMS_PATH]

    @pytest.mark.asyncio
    async def test_the_annotations_request_follows_no_redirect_either(
        self, origin: LoopbackOrigin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")
        items = [{"key": "K1", "data": {"itemType": "journalArticle", "title": "Paper One"}}]
        origin.routes[self._ITEMS_PATH] = page(json.dumps(items), "application/json")
        children_path = "/api/users/u/items/K1/children?format=json&limit=50"
        origin.routes[children_path] = redirect(origin.url("/elsewhere"))
        origin.routes["/elsewhere"] = page("[]", "application/json")

        result, _ = await self._sync(origin.url("/api"))

        assert (result.published, result.failed) == (1, 0)
        assert origin.hits == [self._ITEMS_PATH, children_path]

    @pytest.mark.asyncio
    async def test_a_page_past_the_size_cap_fails_instead_of_parsing_part_of_it(
        self, origin: LoopbackOrigin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")
        monkeypatch.setattr(zotero_module, "_MAX_RESPONSE_BYTES", 1024)
        origin.routes[self._ITEMS_PATH] = page(json.dumps([{"key": "K", "data": {"title": "x" * 2048}}]), "application/json")

        result, publish = await self._sync(origin.url("/api"))

        assert (result.published, result.failed) == (0, 1)
        assert result.errors == ["the response is too large"]
        publish.assert_not_awaited()


def _publish_result(*, written: bool) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(written=written, conflict_skipped=False, skipped=False, absolute_path="/tmp/x")
