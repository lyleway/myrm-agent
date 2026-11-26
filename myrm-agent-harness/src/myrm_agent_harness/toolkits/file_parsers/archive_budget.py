"""Unpacking budget for ZIP containers (docx, xlsx, pptx, epub, odt, ods, odp).

The size of a ZIP file says little about the memory its parser needs: deflate shrinks a run of identical bytes about
a thousand to one, and the sizes in the archive's own headers are written by whoever made the file. The budget is
therefore measured, not read: every member is inflated through the standard-library reader and the bytes that come
out are counted.

[INPUT]
- stdlib zipfile: the reader the parsers use, so the count is what they would get

[OUTPUT]
- UNPACKED_BUDGET_BYTES: the most a container may inflate to, all members together
- MAX_ARCHIVE_MEMBERS: the most entries a container may list
- ArchiveBudgetExceededError: the container is over the budget, or holds a member the budget cannot measure
- check_zip_budget: measure a file on disk; returns when it is within budget or is not a ZIP

[POS]
Mechanism for any caller that opens a ZIP it did not make. The caller decides which files to check and what a
rejection means; the parsers themselves do not call it.
"""

from __future__ import annotations

import contextlib
import copy
import sys
import zipfile
import zlib
from pathlib import Path

# A container of ordinary documents inflates to a few MB; 32 MB leaves room for a large book or a wide spreadsheet.
UNPACKED_BUDGET_BYTES = 32 * 1024 * 1024
# Office files list a few dozen members and an EPUB a few hundred; a count in the thousands is a flood of entries.
MAX_ARCHIVE_MEMBERS = 4096

_READ_CHUNK_BYTES = 64 * 1024
# OOXML, ODF and EPUB allow only these two. The standard-library bzip2 and LZMA readers hand back everything a
# compressed block holds in one piece, so a read in 64 KB steps would still allocate the whole inflated member.
_MEASURABLE_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
# What the reader raises for a member it cannot read. A parser that reads that member meets the same fault, and one
# that does not is not harmed by it, so the budget only counts the bytes the member produced before it failed.
_UNREADABLE_MEMBER_ERRORS = (zipfile.BadZipFile, zlib.error, RuntimeError)


class ArchiveBudgetExceededError(ValueError):
    """A ZIP container lists too many members, inflates past the budget, or uses an unmeasurable compression."""


def check_zip_budget(
    path: str | Path,
    *,
    max_bytes: int = UNPACKED_BUDGET_BYTES,
    max_members: int = MAX_ARCHIVE_MEMBERS,
) -> None:
    """Raise ``ArchiveBudgetExceededError`` when the ZIP at ``path`` is over the budget.

    The file's content decides, not its name or declared type: whatever the label, a ZIP reaches a ZIP parser.
    A file that is not a ZIP is returned untouched. A ZIP the reader cannot open raises its own ``BadZipFile``,
    which is what the parser would raise as well.
    """
    if not zipfile.is_zipfile(path):
        return
    remaining = max_bytes
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > max_members:
            raise ArchiveBudgetExceededError(f"ZIP container holds {len(members)} members; the limit is {max_members}")
        for info in members:
            if info.is_dir():
                continue
            if info.compress_type not in _MEASURABLE_METHODS:
                raise ArchiveBudgetExceededError(
                    f"ZIP container uses compression method {info.compress_type}, which cannot be measured"
                )
            remaining -= _inflated_size(archive, info, limit=remaining)
            if remaining < 0:
                raise ArchiveBudgetExceededError(f"ZIP container inflates past the budget of {max_bytes} bytes")


def _inflated_size(archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, limit: int) -> int:
    """Bytes ``info`` inflates to, counted until they pass ``limit``.

    The reader cuts a member's output at the ``file_size`` its header declares, a number the archive's author
    writes. A copy of the header with that cap lifted makes the count follow the decompressor instead. A member
    that fails loses the read it failed in (at most 64 KB) from the count.
    """
    probe = copy.copy(info)
    probe.file_size = sys.maxsize
    counted = 0
    with contextlib.suppress(*_UNREADABLE_MEMBER_ERRORS), archive.open(probe) as member:
        while counted <= limit and (chunk := member.read(_READ_CHUNK_BYTES)):
            counted += len(chunk)
    return counted
