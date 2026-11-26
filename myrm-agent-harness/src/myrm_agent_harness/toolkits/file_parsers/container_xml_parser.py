"""EPUB and ODF container parsers (zip + XML text extraction).

[INPUT]
- file path: EPUB/ODF 容器文件
- myrm_agent_harness.toolkits.file_parsers.base::FileParser (POS: 解析器基类)

[OUTPUT]
- EpubParser: EPUB 纯文本提取
- OdfParser: OpenDocument 文本提取
- supported_extensions: 各解析器支持的扩展名列表

[POS]
Container format parsing layer for EPUB (XHTML items) and ODF (content.xml) documents.
Consumes zip archives and produces normalized plain text for downstream ingestion.
The archive is read and the XML parsed on a worker thread, so a large container never holds the event loop; the
size of what it may inflate to is bounded by the caller (see archive_budget.check_zip_budget), not here. The time
EPUB tag stripping takes grows linearly with the chapter, however the markup is broken.
"""

from __future__ import annotations

import asyncio
import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from myrm_agent_harness.toolkits.file_parsers.base import FileParser

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(markup: str) -> str:
    """Replace every tag of ``markup`` with a space, in time linear in its length.

    ``_TAG_RE`` tries each "<" against the whole rest of the text, so a run of "<" with no ">" after it costs
    quadratic time (200,000 of them: ~20 s with the interpreter lock held, from a few hundred compressed bytes).
    A "<" past the last ">" can never close, so that tail is passed through and only the head is substituted,
    which leaves the result exactly what the pattern alone would give. ``web_fetch.processing.html_scan.strip_tags``
    is the same scan with an empty replacement; this package cannot import it, because web_fetch imports this one.
    """
    head_end = markup.rfind(">") + 1
    return _TAG_RE.sub(" ", markup[:head_end]) + markup[head_end:]


class EpubParser(FileParser):
    """Extract plain text from EPUB XHTML items."""

    async def parse(self, file_path: str) -> str:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        return await asyncio.to_thread(self._parse_sync, path)

    def _parse_sync(self, path: Path) -> str:
        """Synchronously join the text of every XHTML item, in the order of the sorted member names."""
        chunks: list[str] = []
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if not (name.endswith(".xhtml") or name.endswith(".html")):
                    continue
                raw = archive.read(name).decode("utf-8", errors="ignore")
                normalized = " ".join(_strip_tags(raw).split())
                if normalized:
                    chunks.append(normalized)

        joined = "\n\n".join(chunks)
        logger.info("EPUB parsed: %s, sections=%d", path.name, len(chunks))
        return joined

    @property
    def supported_extensions(self) -> list[str]:
        return [".epub"]


class OdfParser(FileParser):
    """Extract text from OpenDocument ZIP packages."""

    def __init__(self, extension: str) -> None:
        self._extension = extension

    async def parse(self, file_path: str) -> str:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        return await asyncio.to_thread(self._parse_sync, path)

    def _parse_sync(self, path: Path) -> str:
        """Synchronously join the text nodes of ``content.xml``; an empty string when the package has none."""
        with zipfile.ZipFile(path) as archive:
            if "content.xml" not in archive.namelist():
                return ""
            raw_xml = archive.read("content.xml")

        root = ElementTree.fromstring(  # noqa: S314 — expat blocks external entities by default
            raw_xml
        )
        texts = [node.text.strip() for node in root.iter() if node.text and node.text.strip()]
        joined = "\n".join(texts)
        logger.info("ODF parsed: %s, nodes=%d", path.name, len(texts))
        return joined

    @property
    def supported_extensions(self) -> list[str]:
        return [self._extension]
