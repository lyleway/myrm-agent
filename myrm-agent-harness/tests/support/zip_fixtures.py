"""ZIP archives for the unpacking-budget tests: ordinary containers and the hostile ones a page can serve.

Every builder returns the archive's bytes and touches no disk. The hostile ones stay small on purpose: deflate
shrinks a run of zeros about a thousand to one, so a test can state a budget of a few MB and use the shipped
constants in a single case.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from collections.abc import Mapping

_ZEROS_BLOCK = bytes(1024 * 1024)
_CENTRAL_ENTRY = b"PK\x01\x02"
_END_RECORD = struct.Struct("<IHHHHIIH")
_END_RECORD_SIGNATURE = 0x06054B50
# Where the central directory entry keeps the fields a forger rewrites: (offset in the entry, struct format).
_ENTRY_FIELDS = {
    "flag_bits": (8, "<H"),
    "crc": (16, "<I"),
    "compress_size": (20, "<I"),
    "file_size": (24, "<I"),
}
_NAME_LENGTH_OFFSET = 28
_NAME_OFFSET = 46
_LOCAL_HEADER_SIZE = 30
_EPUB_MARKERS = {"mimetype": b"application/epub+zip", "META-INF/container.xml": b"<container/>"}


def build_zip(members: Mapping[str, bytes], *, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    """An archive holding ``members`` in the order given."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def zero_run_zip(
    unpacked_bytes: int,
    *,
    name: str = "word/document.xml",
    method: int = zipfile.ZIP_DEFLATED,
    siblings: Mapping[str, bytes] | None = None,
) -> bytes:
    """An archive whose ``name`` member inflates to ``unpacked_bytes`` zero bytes, with every size in its headers true.

    ``siblings`` are written first, so a bomb can carry the entries that make a sniffer take it for a document.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method, compresslevel=9) as archive:
        for sibling, content in (siblings or {}).items():
            archive.writestr(sibling, content)
        with archive.open(name, "w") as member:
            remaining = unpacked_bytes
            while remaining > 0:
                step = min(remaining, len(_ZEROS_BLOCK))
                member.write(_ZEROS_BLOCK[:step])
                remaining -= step
    return buffer.getvalue()


def epub_bomb(unpacked_bytes: int) -> bytes:
    """An archive every sniffer takes for an EPUB, one XHTML item of which inflates to ``unpacked_bytes``."""
    return zero_run_zip(unpacked_bytes, name="OEBPS/chapter.xhtml", siblings=_EPUB_MARKERS)


def epub_flood(entries: int) -> bytes:
    """An archive every sniffer takes for an EPUB that lists ``entries`` members in total, all of them empty."""
    members = dict(_EPUB_MARKERS)
    members.update({f"OEBPS/item{index}.xhtml": b"" for index in range(entries - len(members))})
    return build_zip(members)


def forge_header(archive: bytes, member: str, **fields: int) -> bytes:
    """``archive`` with fields of ``member``'s central directory entry rewritten (``crc``, ``file_size``, ...)."""
    data = bytearray(archive)
    wanted = member.encode()
    position = data.find(_CENTRAL_ENTRY)
    while position >= 0:
        (name_length,) = struct.unpack_from("<H", data, position + _NAME_LENGTH_OFFSET)
        name_start = position + _NAME_OFFSET
        if bytes(data[name_start : name_start + name_length]) == wanted:
            for field, value in fields.items():
                offset, fmt = _ENTRY_FIELDS[field]
                struct.pack_into(fmt, data, position + offset, value)
            return bytes(data)
        position = data.find(_CENTRAL_ENTRY, position + len(_CENTRAL_ENTRY))
    raise ValueError(f"no central directory entry for {member!r}")


def claim_only_prefix(archive: bytes, member: str, claimed_bytes: int) -> bytes:
    """A self-consistent lie: the headers give the size and the CRC-32 of the member's first ``claimed_bytes`` only."""
    with zipfile.ZipFile(io.BytesIO(archive)) as source, source.open(member) as handle:
        prefix = handle.read(claimed_bytes)
    return forge_header(archive, member, crc=zlib.crc32(prefix), file_size=claimed_bytes)


def scramble_stream(archive: bytes, member: str, *, at: int = 0) -> bytes:
    """``archive`` with 8 bytes of ``member``'s deflate stream, ``at`` bytes in, overwritten with 0xFF."""
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        header_offset = source.getinfo(member).header_offset
    name_length, extra_length = struct.unpack_from("<HH", archive, header_offset + 26)
    damage_start = header_offset + _LOCAL_HEADER_SIZE + name_length + extra_length + at
    return archive[:damage_start] + b"\xff" * 8 + archive[damage_start + 8 :]


def overlapping_entries(unpacked_bytes: int, copies: int) -> bytes:
    """One deflate stream of ``unpacked_bytes`` zeros that ``copies`` central directory entries all point at."""
    data = zero_run_zip(unpacked_bytes, name="big.xhtml")
    _, _, _, _, _, directory_size, directory_offset, _ = _END_RECORD.unpack(data[-_END_RECORD.size :])
    entry = data[directory_offset : directory_offset + directory_size]  # the archive has one member: one entry
    widened = entry * copies
    end_record = _END_RECORD.pack(_END_RECORD_SIGNATURE, 0, 0, copies, copies, len(widened), directory_offset, 0)
    return data[:directory_offset] + widened + end_record


def with_junk_prefix(archive: bytes, junk: bytes = b"not part of the archive " * 8) -> bytes:
    """``archive`` behind bytes that make it start with something other than ``PK``."""
    return junk + archive


def epub(chapters: Mapping[str, str]) -> bytes:
    """A minimal EPUB: the declared ``mimetype``, the container marker and one XHTML item per chapter."""
    members = dict(_EPUB_MARKERS)
    members.update(
        {f"OEBPS/{name}.xhtml": f"<html><body>{text}</body></html>".encode() for name, text in chapters.items()}
    )
    return build_zip(members)


def odt(paragraphs: list[str]) -> bytes:
    """A minimal OpenDocument text file: the declared ``mimetype`` and a ``content.xml`` of one paragraph per entry."""
    body = "".join(f"<text:p>{text}</text:p>" for text in paragraphs)
    content = (
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f"<office:body><office:text>{body}</office:text></office:body></office:document-content>"
    )
    return build_zip({"mimetype": b"application/vnd.oasis.opendocument.text", "content.xml": content.encode()})
