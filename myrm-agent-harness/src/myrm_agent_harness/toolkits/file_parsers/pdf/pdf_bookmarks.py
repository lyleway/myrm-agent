"""PDF bookmark/outline extraction and page resolution.

[INPUT]
- base::PDFHeading (POS: File parser base classes and data structures)
- pdfplumber.PDF: opened pdfplumber document (outline walking and named-destination lookup)

[OUTPUT]
- extract_bookmarks: resolved bookmarks as PDFHeading values (supports /Dest, /A /GoTo, and named destinations)
- bookmarks_are_degenerate: quality gate for generic exporter titles

[POS]
Bookmark source of the PDF heading pipeline, owning outline walking, GoTo action
and named-destination resolution, and page-number mapping.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from myrm_agent_harness.toolkits.file_parsers.base import PDFHeading

if TYPE_CHECKING:
    from pdfplumber.pdf import PDF

logger = logging.getLogger(__name__)

# Titles emitted by generic exporters (slide decks, scans) that carry no
# document structure. A bookmark set dominated by these must not suppress
# text/font detection, and must not be injected as headings.
_GENERIC_TITLE_RE = re.compile(
    r"^(?:幻灯片|slide|page|untitled|sheet|scan(?:ned)?(?: page)?|第\s*\d+\s*页)\s*[\divxlc一二三四五六七八九十]*$",
    re.IGNORECASE,
)
_DEGENERATE_RATIO = 0.6
_DEGENERATE_MIN_BOOKMARKS = 3


def _resolve_obj(obj: object) -> object:
    """Dereference a pdfminer PDFObjRef if present."""
    if hasattr(obj, "resolve"):
        return obj.resolve()
    return obj


def _literal_name(value: object) -> str | None:
    """Return the normalized name of a pdfminer PSLiteral, bytes, or str."""
    name = getattr(value, "name", value)
    if isinstance(name, bytes):
        return name.decode("latin-1", errors="replace")
    if isinstance(name, str):
        return name
    return None


def _extract_dest_from_action(action: object) -> object:
    """Extract destination `/D` from a `/GoTo` PDF action dictionary; return None for other action types."""
    resolved = _resolve_obj(action)
    if not isinstance(resolved, dict):
        return None
    subtype = _literal_name(_resolve_obj(resolved.get("S")))
    if subtype != "GoTo":
        return None
    return resolved.get("D")


def _resolve_dest_array(pdf: PDF, raw_dest: object) -> list[object] | tuple[object, ...] | None:
    """Resolve a direct destination array, dictionary wrapper, or named destination into a dest array."""
    target = _resolve_obj(raw_dest)
    if target is None:
        return None

    # Named destination (bytes, str, or PSLiteral) — must be checked before sequence indexing
    # because indexing `bytes` yields an integer byte value rather than a page reference.
    if isinstance(target, (bytes, str)) or hasattr(target, "name"):
        if not hasattr(pdf.doc, "get_dest"):
            return None
        lookup_key: object = getattr(target, "name", target)
        try:
            target = _resolve_obj(pdf.doc.get_dest(lookup_key))
        except Exception:
            if isinstance(lookup_key, str):
                try:
                    target = _resolve_obj(pdf.doc.get_dest(lookup_key.encode("latin-1")))
                except Exception:
                    return None
            else:
                return None

    if isinstance(target, dict):
        target = _resolve_obj(target.get("D"))

    if isinstance(target, (list, tuple)) and len(target) > 0:
        return target
    return None


def extract_bookmarks(pdf: PDF) -> list[PDFHeading]:
    """Extract resolved bookmarks from the document outline.

    Supports direct `/Dest` arrays, `/A << /S /GoTo /D ... >>` action outlines,
    and named destinations resolved via `pdf.doc.get_dest`. PDFs without outlines
    (`PDFNoOutlines`) return an empty list silently.
    """
    try:
        if not hasattr(pdf, "doc") or not hasattr(pdf.doc, "get_outlines"):
            logger.debug("PDF has no outline/bookmark support")
            return []

        outlines = list(pdf.doc.get_outlines())
        if not outlines:
            logger.debug("PDF has no bookmarks")
            return []

        page_ref_map = _build_page_number_map(pdf)
        total_pages = len(pdf.pages)
        headings: list[PDFHeading] = []

        for level, title, dest, action, _se in outlines:
            if not title or not title.strip():
                continue

            page_num: int | None = None
            try:
                raw_dest = dest if dest is not None else _extract_dest_from_action(action)
                dest_array = _resolve_dest_array(pdf, raw_dest) if raw_dest is not None else None
                if dest_array:
                    page_num = _resolve_bookmark_page(dest_array[0], page_ref_map, total_pages)
            except Exception as e:
                logger.debug("Failed to resolve bookmark '%s': %s", title, e)

            if page_num is None:
                continue

            headings.append(
                PDFHeading(
                    level=min(max(level, 1), 6),
                    title=title.strip(),
                    page_num=page_num,
                )
            )

        return headings

    except Exception as e:
        if type(e).__name__ == "PDFNoOutlines":
            logger.debug("PDF has no outlines")
            return []
        logger.warning("Failed to extract bookmarks: %s", e)
        return []


def bookmarks_are_degenerate(headings: list[PDFHeading]) -> bool:
    """Return True when bookmark titles are generic exporter noise."""
    if len(headings) < _DEGENERATE_MIN_BOOKMARKS:
        return False
    generic = sum(1 for heading in headings if _GENERIC_TITLE_RE.match(heading.title.strip()))
    return generic / len(headings) >= _DEGENERATE_RATIO


def _build_page_number_map(pdf: PDF) -> dict[int, int]:
    """Build lookup from PDF page object IDs to 1-based page numbers.

    pdfminer outlines reference pages by object id. In pdfplumber these are
    exposed as `page.page_obj.pageid` (or legacy `objid`).
    """
    page_ref_map: dict[int, int] = {}

    for idx, page in enumerate(pdf.pages, 1):
        if hasattr(page, "page_obj"):
            for attr in ("pageid", "objid"):
                ref_id = getattr(page.page_obj, attr, None)
                if isinstance(ref_id, int):
                    page_ref_map[ref_id] = idx
                    break

    return page_ref_map


def _resolve_bookmark_page(
    page_ref: object,
    page_ref_map: dict[int, int],
    total_pages: int,
) -> int | None:
    """Resolve bookmark destination to 1-based page number.

    Handles multiple reference formats:
    - Integer object ID (via objid/pageid lookup)
    - Direct integer page index (0-based, converted to 1-based)
    - Lazy-resolved object references
    """
    ref_id = getattr(page_ref, "objid", None)
    if isinstance(ref_id, int):
        return page_ref_map.get(ref_id)

    if isinstance(page_ref, int):
        candidate = page_ref + 1
        if 1 <= candidate <= total_pages:
            return candidate
        return None

    if hasattr(page_ref, "resolve"):
        try:
            resolved = page_ref.resolve()
            for attr in ("pageid", "objid"):
                resolved_id = getattr(resolved, attr, None)
                if isinstance(resolved_id, int):
                    return page_ref_map.get(resolved_id)
        except Exception:
            pass

    return None
