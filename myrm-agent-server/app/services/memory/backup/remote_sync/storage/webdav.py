"""WebDAV backup storage (Jianguoyun, Nextcloud, ownCloud or any WebDAV server) over an async ``httpx`` client.

[INPUT]
httpx::AsyncClient (POS: external HTTP client, imported on first use)
defusedxml.ElementTree::fromstring (POS: XML parser that refuses entity expansion and external references)
app.services.memory.backup.remote_sync.storage.base::RemoteBackupStrategy/RemoteBackupFile/RemoteStorageError (POS: provider contract)

[OUTPUT]
WebDAVBackupConfig / WebDAVBackupStrategy: the WebDAV implementation of the storage contract

[POS]
A missing backup folder (HTTP 404) lists as empty because the first upload creates it; any other listing refusal, any
answer that is not a multistatus document, and any answer larger than ``_MAX_LISTING_BYTES`` raises
``RemoteStorageError``. The answer is read as a stream that stops at that size and parsed as namespaced XML (each
server picks its own prefix for ``DAV:``), in time linear in its size. Listed names are percent-decoded and only direct
``.gz`` children count.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as DefusedElementTree

from app.services.memory.backup.remote_sync.storage.base import (
    RemoteBackupFile,
    RemoteBackupStrategy,
    RemoteStorageError,
)

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

_DAV = "{DAV:}"

# Holding and parsing a listing costs about eight times its size in memory (a 7.9 MiB answer raised the process peak
# by 62 MiB) and an entry is about 300 bytes, so 8 MiB is some 27,000 entries: far above a backup folder that rotates,
# small enough that no single answer can exhaust the process.
_MAX_LISTING_BYTES = 8 * 1024 * 1024


@dataclass
class WebDAVBackupConfig:
    """WebDAV connection settings."""

    host: str = ""
    username: str = ""
    password: str = ""
    path: str = "/myrm-backups"


class WebDAVBackupStrategy(RemoteBackupStrategy):
    """WebDAV-based remote backup strategy.

    Supports: 坚果云, Nextcloud, ownCloud, or any WebDAV server.
    """

    def __init__(self, config: WebDAVBackupConfig) -> None:
        self._config = config
        self._client: "_WebDAVClient | None" = None

    def _get_client(self) -> "_WebDAVClient":
        if self._client is None:
            self._client = _WebDAVClient(
                host=self._config.host,
                username=self._config.username,
                password=self._config.password,
                base_path=self._config.path,
            )
        return self._client

    async def upload(self, local_path: Path, remote_name: str) -> bool:
        client = self._get_client()
        return await client.put_file(local_path, remote_name)

    async def download(self, remote_name: str, local_path: Path) -> bool:
        client = self._get_client()
        return await client.get_file(remote_name, local_path)

    async def list_files(self) -> list[RemoteBackupFile]:
        client = self._get_client()
        return await client.list_files()

    async def delete(self, remote_name: str) -> bool:
        client = self._get_client()
        return await client.delete_file(remote_name)

    async def check_connection(self) -> bool:
        client = self._get_client()
        return await client.check_connection()


class _WebDAVClient:
    """Async WebDAV client using httpx for HTTP operations."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        base_path: str,
    ) -> None:
        self._host = host.rstrip("/")
        self._username = username
        self._password = password
        self._base_path = base_path.strip("/")

    def _url(self, filename: str = "") -> str:
        parts = [self._host]
        if self._base_path:
            parts.append(self._base_path)
        if filename:
            parts.append(filename)
        return "/".join(parts)

    async def _ensure_directory(self, client: "httpx.AsyncClient") -> None:
        """Ensure remote backup directory exists (MKCOL)."""
        if not self._base_path:
            return
        url = f"{self._host}/{self._base_path}/"
        resp = await client.request("MKCOL", url)
        # 201 = created, 405 = already exists, 301 = redirect (exists)
        if resp.status_code not in (201, 405, 301, 207):
            logger.debug("MKCOL %s returned %s (may already exist)", url, resp.status_code)

    async def check_connection(self) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(
                auth=(self._username, self._password),
                timeout=15.0,
                follow_redirects=True,
            ) as client:
                resp = await client.request("PROPFIND", self._url() + "/", headers={"Depth": "0"})
                return resp.status_code in (207, 200, 301)
        except Exception as e:
            logger.warning("WebDAV connection check failed: %s", e)
            return False

    async def put_file(self, local_path: Path, remote_name: str) -> bool:
        import httpx

        try:
            content = local_path.read_bytes()
            async with httpx.AsyncClient(
                auth=(self._username, self._password),
                timeout=300.0,
                follow_redirects=True,
            ) as client:
                await self._ensure_directory(client)
                resp = await client.put(
                    self._url(remote_name),
                    content=content,
                    headers={"Content-Type": "application/octet-stream"},
                )
                success = resp.status_code in (200, 201, 204)
                if not success:
                    logger.error("WebDAV upload failed: %s %s", resp.status_code, resp.text[:200])
                return success
        except Exception as e:
            logger.error("WebDAV upload error: %s", e)
            return False

    async def get_file(self, remote_name: str, local_path: Path) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(
                auth=(self._username, self._password),
                timeout=300.0,
                follow_redirects=True,
            ) as client:
                resp = await client.get(self._url(remote_name))
                if resp.status_code == 200:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_bytes(resp.content)
                    return True
                logger.error("WebDAV download failed: %s", resp.status_code)
                return False
        except Exception as e:
            logger.error("WebDAV download error: %s", e)
            return False

    async def list_files(self) -> list[RemoteBackupFile]:
        import httpx

        propfind_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<propfind xmlns="DAV:">'
            "<prop><getlastmodified/><getcontentlength/></prop>"
            "</propfind>"
        )
        async with (
            httpx.AsyncClient(
                auth=(self._username, self._password),
                timeout=30.0,
                follow_redirects=True,
            ) as client,
            client.stream(
                "PROPFIND",
                self._url() + "/",
                content=propfind_body.encode(),
                headers={"Depth": "1", "Content-Type": "application/xml"},
            ) as resp,
        ):
            if resp.status_code == 404:
                return []  # nothing has been uploaded yet: the first upload creates the folder
            if resp.status_code != 207:
                raise RemoteStorageError(f"WebDAV listing refused: HTTP {resp.status_code}")
            listing = await _read_listing(resp)
        return sorted(_parse_webdav_multistatus(listing), key=lambda f: f.modified_time, reverse=True)

    async def delete_file(self, remote_name: str) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(
                auth=(self._username, self._password),
                timeout=30.0,
                follow_redirects=True,
            ) as client:
                resp = await client.delete(self._url(remote_name))
                return resp.status_code in (200, 204, 404)
        except Exception as e:
            logger.error("WebDAV delete error: %s", e)
            return False


async def _read_listing(resp: "httpx.Response") -> str:
    """The body of a listing answer; raises ``RemoteStorageError`` once it is known to exceed ``_MAX_LISTING_BYTES``.

    The size is checked against the announced length first, then against the bytes actually received, because a server
    may announce nothing (chunked) or announce less than it sends. The answer is never truncated: half a listing would
    make a backup folder look smaller than it is, and rotation would then keep the wrong files.
    """
    announced = resp.headers.get("content-length", "")
    # ``isascii`` first: ``str.isdigit`` also accepts characters such as "²" that ``int`` rejects.
    if announced.isascii() and announced.isdigit() and int(announced) > _MAX_LISTING_BYTES:
        raise _listing_too_large()
    body = bytearray()
    async for chunk in resp.aiter_bytes():
        body += chunk
        if len(body) > _MAX_LISTING_BYTES:
            raise _listing_too_large()
    return body.decode(resp.encoding or "utf-8", errors="replace")


def _listing_too_large() -> RemoteStorageError:
    return RemoteStorageError(
        f"WebDAV listing is larger than {_MAX_LISTING_BYTES // (1024 * 1024)} MiB; "
        "keep fewer files in the backup folder (rotate old backups or use a folder of its own)"
    )


def _property(response: ET.Element, name: str) -> str | None:
    """The first value of the ``DAV:`` property ``name`` in ``response``.

    A server lists the properties it lacks in a second ``propstat`` as empty elements; they carry no value.
    """
    for element in response.iterfind(f"{_DAV}propstat/{_DAV}prop/{_DAV}{name}"):
        if element.text and element.text.strip():
            return element.text.strip()
    return None


def _modified_time(value: str | None) -> datetime:
    """The listed modification time; one that is missing or unreadable is the time of the listing."""
    if value is None:
        return datetime.now(UTC)
    try:
        modified = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return datetime.now(UTC)
    return modified if modified.tzinfo else modified.replace(tzinfo=UTC)


def _size_bytes(value: str | None) -> int:
    try:
        return int(value) if value is not None else 0
    except ValueError:
        return 0


def _parse_webdav_multistatus(xml_text: str) -> list[RemoteBackupFile]:
    """Parse a PROPFIND 207 multistatus answer into the backups it lists.

    Only the elements the protocol puts there are read (``multistatus/response/href`` and
    ``response/propstat/prop/<property>``), so a response nested inside another one is ignored and the work stays
    linear in the size of the answer.
    """
    try:
        root = DefusedElementTree.fromstring(xml_text)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise RemoteStorageError("WebDAV listing is not a readable XML document") from exc
    if root.tag != f"{_DAV}multistatus":
        raise RemoteStorageError("WebDAV listing is not a multistatus document")

    results: list[RemoteBackupFile] = []
    for response in root.iterfind(f"{_DAV}response"):
        href = response.findtext(f"{_DAV}href", default="").strip()
        filename = unquote(href.rstrip("/").rsplit("/", 1)[-1])  # hrefs are percent-encoded
        # An encoded separator would turn a direct child into a path that leaves the backup folder.
        if not filename or "/" in filename or not filename.endswith(".gz"):
            continue
        results.append(
            RemoteBackupFile(
                file_name=filename,
                modified_time=_modified_time(_property(response, "getlastmodified")),
                size_bytes=_size_bytes(_property(response, "getcontentlength")),
            )
        )
    return results
