"""route_binary_content: a ZIP container is measured by its content before any parser opens it."""

from __future__ import annotations

import io
import logging
import threading
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from myrm_agent_harness.toolkits.file_parsers import archive_budget
from myrm_agent_harness.toolkits.file_parsers.archive_budget import MAX_ARCHIVE_MEMBERS, UNPACKED_BUDGET_BYTES
from myrm_agent_harness.toolkits.web_fetch.processing import binary_router
from myrm_agent_harness.toolkits.web_fetch.processing.binary_router import route_binary_content
from tests.support.zip_fixtures import (
    build_zip,
    claim_only_prefix,
    epub,
    epub_bomb,
    epub_flood,
    forge_header,
    odt,
    with_junk_prefix,
)

_URL = "https://example.com/file"
_CHAPTER = "OEBPS/chapter.xhtml"
_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# However the page labels the body, a ZIP container reaches a ZIP parser: the parser follows the bytes.
_LABELS = {
    "no_label": {},
    "octet_stream": {"content-type": "application/octet-stream"},
    "pdf_type": {"content-type": "application/pdf"},
    "png_type": {"content-type": "image/png"},
    "text_type": {"content-type": "text/plain"},
    "docx_type": {"content-type": _DOCX_TYPE},
    "pdf_filename": {"content-disposition": 'attachment; filename="report.pdf"'},
    "txt_filename": {"content-disposition": 'attachment; filename="notes.txt"'},
}


@pytest.fixture(scope="module")
def bomb() -> bytes:
    """An EPUB (as every sniffer sees it) that inflates a megabyte past the shipped budget."""
    return epub_bomb(UNPACKED_BUDGET_BYTES + 1024 * 1024)


@pytest.fixture
def parser_lookup() -> Iterator[MagicMock]:
    """Stands in for ``get_parser``: a refusal leaves it uncalled, so no parser ever sees the bytes."""
    stub = MagicMock()
    stub.parse = AsyncMock(return_value="parsed text")
    with patch("myrm_agent_harness.toolkits.file_parsers.get_parser", return_value=stub) as lookup:
        yield lookup


class TestHostileContainers:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("headers", _LABELS.values(), ids=_LABELS.keys())
    async def test_a_bomb_is_refused_whatever_it_is_labelled(
        self, bomb: bytes, headers: dict[str, str], parser_lookup: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=binary_router.logger.name):
            result = await route_binary_content(bomb, headers, _URL)

        assert result is None
        parser_lookup.assert_not_called()
        assert f"inflates past the budget of {UNPACKED_BUDGET_BYTES} bytes" in caplog.text

    @pytest.mark.asyncio
    async def test_a_bomb_behind_junk_bytes_is_refused(self, bomb: bytes, parser_lookup: MagicMock) -> None:
        result = await route_binary_content(with_junk_prefix(bomb), {"content-type": _DOCX_TYPE}, _URL)

        assert result is None
        parser_lookup.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_header_that_understates_the_size_is_refused(self, bomb: bytes, parser_lookup: MagicMock) -> None:
        result = await route_binary_content(forge_header(bomb, _CHAPTER, file_size=1000), {}, _URL)

        assert result is None
        parser_lookup.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_self_consistent_lie_is_refused(self, bomb: bytes, parser_lookup: MagicMock) -> None:
        result = await route_binary_content(claim_only_prefix(bomb, _CHAPTER, claimed_bytes=1000), {}, _URL)

        assert result is None
        parser_lookup.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_flood_of_entries_is_refused(
        self, parser_lookup: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=binary_router.logger.name):
            result = await route_binary_content(epub_flood(MAX_ARCHIVE_MEMBERS + 1), {}, _URL)

        assert result is None
        parser_lookup.assert_not_called()
        assert f"holds {MAX_ARCHIVE_MEMBERS + 1} members; the limit is {MAX_ARCHIVE_MEMBERS}" in caplog.text


class TestFilesThatAreNotZipContainers:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("body", "content_type"),
        [
            (b"%PDF-1.4 a body no parser has to open", "application/pdf"),
            (b"\x89PNG\r\n\x1a\n" + bytes(64), "image/png"),
            (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(64), "application/msword"),
        ],
        ids=["pdf", "png", "legacy_word"],
    )
    async def test_they_reach_their_parser(self, parser_lookup: MagicMock, body: bytes, content_type: str) -> None:
        result = await route_binary_content(body, {"content-type": content_type}, _URL)

        assert result is not None
        assert result.page_content == "parsed text"
        parser_lookup.assert_called_once()


class TestOrdinaryContainers:
    @pytest.mark.asyncio
    async def test_an_epub_is_parsed(self) -> None:
        result = await route_binary_content(epub({"one": "First chapter.", "two": "Second chapter."}), {}, _URL)

        assert result is not None
        assert result.page_content == "First chapter.\n\nSecond chapter."
        assert result.metadata["source_type"] == "binary_epub"

    @pytest.mark.asyncio
    async def test_an_opendocument_text_is_parsed(self) -> None:
        result = await route_binary_content(odt(["A heading", "Some body text"]), {}, _URL)

        assert result is not None
        assert result.page_content == "A heading\nSome body text"
        assert result.metadata["source_type"] == "binary_odt"

    @pytest.mark.asyncio
    async def test_a_word_document_a_workbook_and_a_presentation_are_parsed(self) -> None:
        docx = pytest.importorskip("docx")
        openpyxl = pytest.importorskip("openpyxl")
        pptx = pytest.importorskip("pptx")

        word = docx.Document()
        word.add_paragraph("A paragraph in a Word file.")
        workbook = openpyxl.Workbook()
        workbook.active["A1"] = "A cell in a workbook"
        slides = pptx.Presentation()
        slides.slides.add_slide(slides.slide_layouts[5]).shapes.title.text = "A title on a slide"
        expected = {
            ".docx": "A paragraph in a Word file.",
            ".xlsx": "A cell in a workbook",
            ".pptx": "A title on a slide",
        }

        for extension, document in {".docx": word, ".xlsx": workbook, ".pptx": slides}.items():
            body = io.BytesIO()
            document.save(body)
            result = await route_binary_content(
                body.getvalue(), {"content-disposition": f'attachment; filename="f{extension}"'}, _URL
            )

            assert result is not None, extension
            assert expected[extension] in result.page_content


class TestUnreadableMembers:
    @staticmethod
    def _epub_with_cover() -> bytes:
        return build_zip(
            {
                "mimetype": b"application/epub+zip",
                "META-INF/container.xml": b"<container/>",
                _CHAPTER: b"<html><body>The chapter text.</body></html>",
                "OEBPS/cover.jpg": b"\xff\xd8\xff" + bytes(200),
            }
        )

    @pytest.mark.asyncio
    async def test_a_member_the_parser_never_reads_does_not_stop_the_container(self) -> None:
        result = await route_binary_content(
            forge_header(self._epub_with_cover(), "OEBPS/cover.jpg", flag_bits=1), {}, _URL
        )

        assert result is not None
        assert result.page_content == "The chapter text."

    @pytest.mark.asyncio
    async def test_a_member_the_parser_needs_fails_as_it_always_did(self, caplog: pytest.LogCaptureFixture) -> None:
        archive = forge_header(self._epub_with_cover(), _CHAPTER, flag_bits=1)

        with caplog.at_level(logging.WARNING, logger=binary_router.logger.name):
            result = await route_binary_content(archive, {}, _URL)

        assert result is None
        assert "encrypted" in caplog.text


class TestOffTheEventLoop:
    @pytest.mark.asyncio
    async def test_detection_and_measurement_run_on_worker_threads(
        self, monkeypatch: pytest.MonkeyPatch, parser_lookup: MagicMock
    ) -> None:
        threads: dict[str, str] = {}
        detect, measure = binary_router.detect_binary_extension, archive_budget.check_zip_budget

        def detecting(headers: dict[str, str], body: bytes) -> str | None:
            threads["detect"] = threading.current_thread().name
            return detect(headers, body)

        def measuring(path: str) -> None:
            threads["measure"] = threading.current_thread().name
            measure(path)

        monkeypatch.setattr(binary_router, "detect_binary_extension", detecting)
        monkeypatch.setattr(archive_budget, "check_zip_budget", measuring)

        await route_binary_content(epub({"one": "text"}), {}, _URL)

        loop_thread = threading.current_thread().name
        assert threads.keys() == {"detect", "measure"}
        assert loop_thread not in threads.values()
