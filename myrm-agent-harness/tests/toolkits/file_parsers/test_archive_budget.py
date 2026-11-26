"""check_zip_budget: the unpacking budget counts what a container really inflates to, whatever its headers say."""

from __future__ import annotations

import copy
import io
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.file_parsers.archive_budget import (
    MAX_ARCHIVE_MEMBERS,
    UNPACKED_BUDGET_BYTES,
    ArchiveBudgetExceededError,
    check_zip_budget,
)
from tests.support.zip_fixtures import (
    build_zip,
    claim_only_prefix,
    epub,
    forge_header,
    overlapping_entries,
    scramble_stream,
    with_junk_prefix,
    zero_run_zip,
)

_MB = 1024 * 1024
_CHUNK = 64 * 1024
_BOMB = "word/document.xml"


def _file(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "container.bin"
    path.write_bytes(data)
    return path


def _entries(count: int) -> bytes:
    return build_zip({f"entry{index}": b"" for index in range(count)}, method=zipfile.ZIP_STORED)


class TestWhatIsMeasured:
    @pytest.mark.parametrize(
        "body",
        [
            b"%PDF-1.7 not a container",
            b"\x89PNG\r\n\x1a\n" + bytes(64),
            b"plain text, nothing to unpack",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(64),  # OLE2: .doc, .xls and .ppt are not ZIPs
            b"",
        ],
        ids=["pdf", "png", "text", "ole2", "empty"],
    )
    def test_a_file_that_is_not_a_zip_is_left_alone(self, tmp_path: Path, body: bytes) -> None:
        check_zip_budget(_file(tmp_path, body))

    def test_an_ordinary_container_is_within_the_budget(self, tmp_path: Path) -> None:
        check_zip_budget(_file(tmp_path, epub({"one": "a chapter", "two": "another chapter"})))

    def test_a_directory_entry_counts_toward_the_member_limit_only(self, tmp_path: Path) -> None:
        path = _file(tmp_path, build_zip({"OEBPS/": b"", "OEBPS/a.xml": b"x" * 100}))
        check_zip_budget(path, max_bytes=100, max_members=2)
        with pytest.raises(ArchiveBudgetExceededError, match="holds 2 members"):
            check_zip_budget(path, max_members=1)

    def test_the_container_is_measured_by_content_not_by_name(self, tmp_path: Path) -> None:
        path = tmp_path / "cat-picture.png"
        path.write_bytes(zero_run_zip(4 * _MB))
        with pytest.raises(ArchiveBudgetExceededError):
            check_zip_budget(path, max_bytes=_MB)

    def test_a_zip_behind_junk_bytes_is_still_measured(self, tmp_path: Path) -> None:
        archive = with_junk_prefix(zero_run_zip(4 * _MB))
        assert not archive.startswith(b"PK") and zipfile.is_zipfile(io.BytesIO(archive))
        with pytest.raises(ArchiveBudgetExceededError):
            check_zip_budget(_file(tmp_path, archive), max_bytes=_MB)

    def test_a_zip_the_reader_cannot_open_raises_the_readers_own_error(self, tmp_path: Path) -> None:
        archive = bytearray(build_zip({"a.xml": b"x"}))
        archive[archive.index(b"PK\x01\x02") + 3] = 0  # the end record is intact, the directory entry is not
        with pytest.raises(zipfile.BadZipFile):
            check_zip_budget(_file(tmp_path, bytes(archive)))


class TestBytesAreCounted:
    def test_an_honest_bomb_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ArchiveBudgetExceededError, match=f"budget of {_MB} bytes"):
            check_zip_budget(_file(tmp_path, zero_run_zip(4 * _MB)), max_bytes=_MB)

    @pytest.mark.parametrize(("unpacked", "accepted"), [(_MB - 1, True), (_MB, True), (_MB + 1, False)])
    def test_the_budget_is_inclusive(self, tmp_path: Path, unpacked: int, accepted: bool) -> None:
        path = _file(tmp_path, zero_run_zip(unpacked))
        if accepted:
            check_zip_budget(path, max_bytes=_MB)
        else:
            with pytest.raises(ArchiveBudgetExceededError):
                check_zip_budget(path, max_bytes=_MB)

    def test_members_are_added_up(self, tmp_path: Path) -> None:
        half = b"x" * (_MB // 2 + 1)
        with pytest.raises(ArchiveBudgetExceededError):
            check_zip_budget(_file(tmp_path, build_zip({"a.xml": half, "b.xml": half})), max_bytes=_MB)

    @pytest.mark.parametrize(
        ("unpacked", "accepted"), [(UNPACKED_BUDGET_BYTES, True), (UNPACKED_BUDGET_BYTES + 1, False)]
    )
    def test_the_shipped_budget_is_inclusive_too(self, tmp_path: Path, unpacked: int, accepted: bool) -> None:
        path = _file(tmp_path, zero_run_zip(unpacked))
        if accepted:
            check_zip_budget(path)
        else:
            with pytest.raises(ArchiveBudgetExceededError, match=str(UNPACKED_BUDGET_BYTES)):
                check_zip_budget(path)

    def test_counting_stops_soon_after_the_budget_is_passed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inflated = 0
        read = zipfile.ZipExtFile.read

        def counting_read(member: zipfile.ZipExtFile, size: int = -1) -> bytes:
            nonlocal inflated
            chunk = read(member, size)
            inflated += len(chunk)
            return chunk

        path = _file(tmp_path, zero_run_zip(64 * _MB))
        monkeypatch.setattr(zipfile.ZipExtFile, "read", counting_read)
        with pytest.raises(ArchiveBudgetExceededError):
            check_zip_budget(path, max_bytes=_MB)

        assert _MB < inflated <= _MB + _CHUNK


class TestHeadersAreNotTrusted:
    def test_a_header_that_understates_the_size_does_not_hide_the_bomb(self, tmp_path: Path) -> None:
        archive = forge_header(zero_run_zip(4 * _MB), _BOMB, file_size=1000)
        with pytest.raises(ArchiveBudgetExceededError):
            check_zip_budget(_file(tmp_path, archive), max_bytes=_MB)

    def test_a_self_consistent_lie_does_not_hide_the_bomb(self, tmp_path: Path) -> None:
        archive = claim_only_prefix(zero_run_zip(4 * _MB), _BOMB, claimed_bytes=1000)
        with zipfile.ZipFile(io.BytesIO(archive)) as forged:
            assert len(forged.read(_BOMB)) == 1000  # the stock reader believes it: size and CRC-32 agree
        with pytest.raises(ArchiveBudgetExceededError):
            check_zip_budget(_file(tmp_path, archive), max_bytes=_MB)

    def test_the_reader_still_cuts_a_members_output_at_the_declared_size(self) -> None:
        """The budget lifts that cut on a copy of the header; if CPython stops applying it, review the budget."""
        archive = claim_only_prefix(zero_run_zip(2 * _MB), _BOMB, claimed_bytes=1000)
        with zipfile.ZipFile(io.BytesIO(archive)) as forged:
            info = forged.getinfo(_BOMB)
            with forged.open(info) as stock:
                assert len(stock.read()) == 1000

            lifted = copy.copy(info)
            lifted.file_size = sys.maxsize
            counted = 0
            with forged.open(lifted) as probe, pytest.raises(zipfile.BadZipFile, match="CRC"):
                while chunk := probe.read(_CHUNK):
                    counted += len(chunk)
            # The stream really holds 2 MB; only the forged CRC-32 fails, at its very end, and costs the last chunk.
            assert counted == 2 * _MB - _CHUNK

    @pytest.mark.filterwarnings("ignore:Overlapped entries:UserWarning")
    def test_entries_that_share_one_stream_are_each_counted(self, tmp_path: Path) -> None:
        archive = overlapping_entries(_MB, copies=8)
        with zipfile.ZipFile(io.BytesIO(archive)) as shared:
            assert len(shared.infolist()) == 8
        with pytest.raises(ArchiveBudgetExceededError):
            check_zip_budget(_file(tmp_path, archive), max_bytes=4 * _MB)


class TestMemberCount:
    @pytest.mark.parametrize(("members", "accepted"), [(100, True), (101, False)])
    def test_the_member_limit_is_inclusive(self, tmp_path: Path, members: int, accepted: bool) -> None:
        path = _file(tmp_path, _entries(members))
        if accepted:
            check_zip_budget(path, max_members=100)
        else:
            with pytest.raises(ArchiveBudgetExceededError, match="holds 101 members; the limit is 100"):
                check_zip_budget(path, max_members=100)

    @pytest.mark.parametrize(("members", "accepted"), [(MAX_ARCHIVE_MEMBERS, True), (MAX_ARCHIVE_MEMBERS + 1, False)])
    def test_the_shipped_member_limit_is_inclusive_too(self, tmp_path: Path, members: int, accepted: bool) -> None:
        path = _file(tmp_path, _entries(members))
        if accepted:
            check_zip_budget(path)
        else:
            with pytest.raises(ArchiveBudgetExceededError, match=f"limit is {MAX_ARCHIVE_MEMBERS}"):
                check_zip_budget(path)


class TestUnmeasurableCompression:
    @pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA], ids=["bzip2", "lzma"])
    def test_a_member_the_reader_would_inflate_in_one_piece_is_refused_unread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: int
    ) -> None:
        """Their decompressors return a whole block at once, so reading in 64 KB steps would still fill memory."""
        path = _file(
            tmp_path, zero_run_zip(2 * _MB, method=method)
        )  # far below the budget: only the method is at fault

        def refuse_to_open(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("the member was opened")

        monkeypatch.setattr(zipfile.ZipFile, "open", refuse_to_open)
        with pytest.raises(ArchiveBudgetExceededError, match=f"compression method {method}"):
            check_zip_budget(path)


def _two_members() -> bytes:
    return build_zip({"a.xml": b"x" * 100, "b.jpg": bytes(range(256)) * 40})


class TestUnreadableMembers:
    @pytest.mark.parametrize(
        ("archive", "fault"),
        [
            (forge_header(_two_members(), "b.jpg", crc=zlib.crc32(b"other")), zipfile.BadZipFile),
            (forge_header(_two_members(), "b.jpg", flag_bits=1), RuntimeError),
            (scramble_stream(_two_members(), "b.jpg"), zlib.error),
        ],
        ids=["bad_crc", "encrypted", "damaged_stream"],
    )
    def test_an_unreadable_member_is_not_over_the_budget(
        self, tmp_path: Path, archive: bytes, fault: type[Exception]
    ) -> None:
        """The parser meets the same fault if it reads that member; one that never reads it is not harmed by it."""
        with zipfile.ZipFile(io.BytesIO(archive)) as stock, pytest.raises(fault):
            stock.read("b.jpg")

        check_zip_budget(_file(tmp_path, archive))

    def test_what_a_member_produced_before_it_failed_still_counts(self, tmp_path: Path) -> None:
        intact = zero_run_zip(8 * _MB)
        with zipfile.ZipFile(io.BytesIO(intact)) as source:
            stream_size = source.getinfo(_BOMB).compress_size
        archive = scramble_stream(intact, _BOMB, at=stream_size // 2)
        with zipfile.ZipFile(io.BytesIO(archive)) as stock, pytest.raises(zlib.error):
            stock.read(_BOMB)

        with pytest.raises(ArchiveBudgetExceededError):
            check_zip_budget(_file(tmp_path, archive), max_bytes=_MB)
