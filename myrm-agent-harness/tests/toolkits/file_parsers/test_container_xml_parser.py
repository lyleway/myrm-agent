"""EpubParser and OdfParser: the text they extract, and that the archive is read on a worker thread."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.file_parsers.base import FileParser
from myrm_agent_harness.toolkits.file_parsers.container_xml_parser import EpubParser, OdfParser
from tests.support.zip_fixtures import build_zip, epub, odt


def _file(tmp_path: Path, data: bytes, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


class TestEpubParser:
    @pytest.mark.asyncio
    async def test_the_items_are_read_in_name_order_with_markup_and_blanks_removed(self, tmp_path: Path) -> None:
        archive = epub({"b": "Second <b>bold</b> text", "a": "First\n\n  chapter"})

        text = await EpubParser().parse(_file(tmp_path, archive, "book.epub"))

        assert text == "First chapter\n\nSecond bold text"

    @pytest.mark.asyncio
    async def test_only_xhtml_and_html_members_with_text_count(self, tmp_path: Path) -> None:
        archive = build_zip(
            {
                "mimetype": b"application/epub+zip",
                "OEBPS/one.html": b"<p>Kept</p>",
                "OEBPS/blank.xhtml": b"<html><body>  </body></html>",
                "OEBPS/style.css": b"p { color: red }",
            }
        )

        assert await EpubParser().parse(_file(tmp_path, archive, "book.epub")) == "Kept"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "markup",
        [
            "plain <b>text</b> here",
            "<!-- <p>commented --> shown",
            "if a < b then <i>stop</i>",
            "<><p>after an empty tag</p>",
            "closed <i>tag</i> then <unterminated",
            "<p>one</p> tail <",
            "no tag at all",
            "<<<>>>",
            "",
        ],
    )
    async def test_markup_is_stripped_exactly_as_the_plain_tag_pattern_would(self, tmp_path: Path, markup: str) -> None:
        archive = build_zip({"OEBPS/one.xhtml": markup.encode()})
        expected = " ".join(re.sub(r"<[^>]+>", " ", markup).split())

        assert await EpubParser().parse(_file(tmp_path, archive, "book.epub")) == expected

    @pytest.mark.asyncio
    async def test_a_run_of_unterminated_tags_is_read_in_linear_time(self, tmp_path: Path) -> None:
        # A tag pattern that rescans the rest of the chapter from every "<" takes ~20 s on 200,000 of them (a few
        # hundred bytes once compressed) and holds the interpreter lock throughout; the bound is far above the
        # linear cost and far below the quadratic one. No ">" may follow the run, or the first "<" ends it at once.
        archive = build_zip({"OEBPS/one.xhtml": ("<" * 200_000 + " tail").encode()})
        started = time.perf_counter()

        text = await EpubParser().parse(_file(tmp_path, archive, "book.epub"))

        assert time.perf_counter() - started < 2.0
        assert text.endswith(" tail")

    @pytest.mark.asyncio
    async def test_a_missing_file_is_reported_by_name(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "absent.epub")

        with pytest.raises(FileNotFoundError, match=r"absent\.epub"):
            await EpubParser().parse(missing)

    def test_it_serves_the_epub_extension(self) -> None:
        assert EpubParser().supported_extensions == [".epub"]


class TestOdfParser:
    @pytest.mark.asyncio
    async def test_the_text_nodes_of_content_xml_are_joined_by_lines(self, tmp_path: Path) -> None:
        archive = odt(["  A heading  ", "Some body text"])

        text = await OdfParser(".odt").parse(_file(tmp_path, archive, "letter.odt"))

        assert text == "A heading\nSome body text"

    @pytest.mark.asyncio
    async def test_a_package_without_content_xml_has_no_text(self, tmp_path: Path) -> None:
        archive = build_zip({"mimetype": b"application/vnd.oasis.opendocument.text"})

        assert await OdfParser(".odt").parse(_file(tmp_path, archive, "empty.odt")) == ""

    @pytest.mark.asyncio
    async def test_a_missing_file_is_reported_by_name(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match=r"absent\.ods"):
            await OdfParser(".ods").parse(str(tmp_path / "absent.ods"))

    def test_it_serves_the_extension_it_was_built_for(self) -> None:
        assert OdfParser(".ods").supported_extensions == [".ods"]


@pytest.fixture(params=["epub", "odf"])
def container(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[FileParser, str]:
    """A container parser together with a small file of the kind it reads."""
    if request.param == "epub":
        parser, archive, name = EpubParser(), epub({"one": "text"}), "book.epub"
    else:
        parser, archive, name = OdfParser(".odt"), odt(["text"]), "letter.odt"
    return parser, _file(tmp_path, archive, name)


class TestOffTheEventLoop:
    @pytest.mark.asyncio
    async def test_the_archive_is_read_on_a_worker_thread(
        self, container: tuple[FileParser, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parser, path = container
        parse_sync = vars(type(parser))["_parse_sync"]
        threads: list[str] = []

        def recording(self: FileParser, file: Path) -> str:
            threads.append(threading.current_thread().name)
            return parse_sync(self, file)

        monkeypatch.setattr(type(parser), "_parse_sync", recording)

        await parser.parse(path)

        assert len(threads) == 1
        assert threads[0] != threading.current_thread().name

    @pytest.mark.asyncio
    async def test_the_event_loop_keeps_ticking_while_an_archive_is_read(
        self, container: tuple[FileParser, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parser, path = container
        parse_sync = vars(type(parser))["_parse_sync"]

        def slow(self: FileParser, file: Path) -> str:
            time.sleep(0.4)  # stands in for a large container
            return parse_sync(self, file)

        monkeypatch.setattr(type(parser), "_parse_sync", slow)
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        try:
            await parser.parse(path)
        finally:
            ticker_task.cancel()

        # A blocked loop records no tick at all; a responsive one records dozens (the bound is deliberately lax).
        assert ticks >= 10
