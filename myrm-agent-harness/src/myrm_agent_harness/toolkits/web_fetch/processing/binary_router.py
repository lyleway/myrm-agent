"""Binary content detection and routing.

When HttpFetcher receives a non-text response (PDF, image, Office documents),
this module detects the content type via a three-layer strategy and routes
the raw bytes to the appropriate file parser for extraction.

[INPUT]
- toolkits.file_parsers (POS: File parsers toolkit entry point.)
- toolkits.file_parsers.archive_budget::check_zip_budget (POS: Unpacking budget for ZIP containers)
- utils.content_sniff::magic_extension (POS: Format signature to file extension)
- web_fetch.processing.pipeline::SOURCE_TRUNCATED_KEY (POS: Document metadata flag for a source cut short)

[OUTPUT]
- MAX_BINARY_SIZE: the largest file body the router parses; a larger one is dropped
- MAX_PARSED_CHARS: the most parsed text a Document keeps unless the caller sets its own cap; a longer text is cut and the Document carries SOURCE_TRUNCATED_KEY
- route_binary_content: Detect and parse binary content from a FetchResult

[POS]
Binary content detection and routing for non-HTML URLs (PDF, images, documents).
Three-layer detection: Content-Type → Content-Disposition → Magic Bytes.
A body that is a ZIP container is measured before any parser opens it: the page controls the sizes inside the
archive, so the 20 MB body cap does not bound what it unpacks to.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path

from langchain_core.documents import Document

from myrm_agent_harness.utils.content_sniff import magic_extension

from .pipeline import SOURCE_TRUNCATED_KEY

logger = logging.getLogger(__name__)

MAX_BINARY_SIZE = 20 * 1024 * 1024  # 20 MB safety limit
# A parsed file can hold far more text than a prompt can use; the Document keeps the beginning and says it was cut.
# A caller that can read the rest from elsewhere passes a larger ``max_chars`` to route_binary_content.
MAX_PARSED_CHARS = 100_000

_CONTENT_TYPE_MAP: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "image/png": ".png",
    "image/jpeg": ".jpeg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}

_CD_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8''|utf-8'')?[\"']?([^\"';\s]+)", re.IGNORECASE)


def _detect_extension_from_content_type(content_type: str) -> str | None:
    """Layer 1: Map Content-Type header to file extension."""
    ct = content_type.split(";")[0].strip().lower()
    return _CONTENT_TYPE_MAP.get(ct)


def _detect_extension_from_disposition(headers: dict[str, str]) -> str | None:
    """Layer 2: Extract filename from Content-Disposition header."""
    cd = headers.get("content-disposition") or headers.get("Content-Disposition") or ""
    match = _CD_FILENAME_RE.search(cd)
    if not match:
        return None
    filename = match.group(1).strip("\"'")
    ext = Path(filename).suffix.lower()
    return ext if ext else None


def detect_binary_extension(headers: dict[str, str], data: bytes) -> str | None:
    """Three-layer detection: CT → CD → Magic. Returns extension or None."""
    ct = headers.get("content-type") or headers.get("Content-Type") or ""
    ext = _detect_extension_from_content_type(ct)
    if ext:
        return ext

    ext = _detect_extension_from_disposition(headers)
    if ext:
        return ext

    return magic_extension(data)


async def route_binary_content(
    raw_body: bytes,
    headers: dict[str, str],
    url: str,
    *,
    max_chars: int | None = None,
) -> Document | None:
    """Route binary content to appropriate parser and return a Document.

    The parsed text is cut at ``max_chars`` (``MAX_PARSED_CHARS`` when ``None``): a caller that can read the rest from
    a stored copy asks for more than one that puts the text into a prompt as it is.

    Returns None if the content type is unrecognized, no parser supports it, a ZIP container is over the unpacking
    budget, or parsing fails.
    """
    if len(raw_body) > MAX_BINARY_SIZE:
        logger.warning("Binary content too large (%d bytes), skipping: %s", len(raw_body), url[:100])
        return None

    # The last detection layer opens a ZIP's central directory, whose size the page controls: keep it off the loop.
    ext = await asyncio.to_thread(detect_binary_extension, headers, raw_body)
    if not ext:
        logger.info("Unrecognized binary format for %s, skipping", url[:100])
        return None

    from myrm_agent_harness.toolkits.file_parsers import is_supported

    if not is_supported(f"file{ext}"):
        logger.info("No parser available for extension %s: %s", ext, url[:100])
        return None

    tmp_file = None
    try:
        tmp_file = tempfile.NamedTemporaryFile(suffix=ext, delete=False)  # noqa: SIM115  manual lifecycle: reused by path after close, removed in finally
        tmp_file.write(raw_body)
        tmp_file.flush()
        tmp_file.close()

        from myrm_agent_harness.toolkits.file_parsers import get_parser
        from myrm_agent_harness.toolkits.file_parsers.archive_budget import check_zip_budget

        # By content, not by label: whatever the headers say, a ZIP reaches a ZIP parser (get_parser sniffs the
        # bytes), and the 20 MB cap above counts compressed bytes only. A refusal raises into the handler below.
        await asyncio.to_thread(check_zip_budget, tmp_file.name)
        parser = get_parser(tmp_file.name)
        result = await parser.parse(tmp_file.name)

        if not result or not result.strip():
            return None

        metadata: dict[str, str | bool] = {
            "url": url,
            "source_type": f"binary_{ext.lstrip('.')}",
            "extension": ext,
        }
        limit = MAX_PARSED_CHARS if max_chars is None else max_chars
        if len(result) > limit:
            logger.info("Parsed text cut at %d of %d characters: %s", limit, len(result), url[:100])
            metadata[SOURCE_TRUNCATED_KEY] = True

        return Document(page_content=result[:limit], metadata=metadata)
    except Exception as e:
        logger.warning("Failed to parse binary content from %s: %s", url[:100], e)
        return None
    finally:
        if tmp_file:
            Path(tmp_file.name).unlink(missing_ok=True)
