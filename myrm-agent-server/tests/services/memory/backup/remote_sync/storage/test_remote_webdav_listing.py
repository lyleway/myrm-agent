"""WebDAV listing: an unreadable remote is never reported as an empty folder, and one answer cannot exhaust the process.

[INPUT]
app.services.memory.backup.remote_sync.storage::WebDAVBackupStrategy/WebDAVBackupConfig/RemoteStorageError
app.services.memory.backup.remote_sync.storage.webdav::_MAX_LISTING_BYTES (POS: the largest listing answer that is read)

[OUTPUT]
That a listing which the server refuses or cannot answer raises, while a folder that does not exist yet lists as empty;
that an answer over the size limit raises as soon as that is known and is never turned into a shorter listing.

[POS]
The real WebDAV client over an in-process ``httpx`` transport; nothing else is replaced. The S3 counterpart lives in
``test_remote_s3_client.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from app.services.memory.backup.remote_sync.storage import RemoteStorageError, WebDAVBackupConfig, WebDAVBackupStrategy, webdav

_NAME = "myrm.20250101120000.laptop.1a2b3c4d.linux.json.gz"
_MULTISTATUS = f"""<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/myrm-backups/</d:href>
    <d:propstat><d:prop><d:getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</d:getlastmodified><d:getcontentlength>0</d:getcontentlength></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/myrm-backups/{_NAME}</d:href>
    <d:propstat><d:prop><d:getlastmodified>Sat, 01 Jan 2025 12:00:00 GMT</d:getlastmodified><d:getcontentlength>4096</d:getcontentlength></d:prop></d:propstat>
  </d:response>
</d:multistatus>"""


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    real_client = httpx.AsyncClient

    def client(**options: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **options)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", client)


def _strategy() -> WebDAVBackupStrategy:
    return WebDAVBackupStrategy(
        WebDAVBackupConfig(host="https://dav.example.test", username="u", password="p", path="/myrm-backups")
    )


async def test_a_listing_reads_the_backups_in_the_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, lambda request: httpx.Response(207, text=_MULTISTATUS))

    files = await _strategy().list_files()

    assert [(f.file_name, f.size_bytes) for f in files] == [(_NAME, 4096)]


async def test_a_folder_that_does_not_exist_yet_lists_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # The first upload creates it; reading a fresh setup must not look like an error.
    _serve(monkeypatch, lambda request: httpx.Response(404))

    assert await _strategy().list_files() == []


@pytest.mark.parametrize("status_code", [401, 403, 500, 503])
async def test_a_refused_listing_raises_instead_of_looking_like_an_empty_folder(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    _serve(monkeypatch, lambda request: httpx.Response(status_code))

    with pytest.raises(RemoteStorageError, match=str(status_code)):
        await _strategy().list_files()


@pytest.mark.parametrize("body", ["", "Bad Gateway", "<html><body>Bad Gateway</body></html>"])
async def test_an_answer_that_is_not_a_multistatus_document_raises_instead_of_looking_like_an_empty_folder(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    _serve(monkeypatch, lambda request: httpx.Response(207, text=body))

    with pytest.raises(RemoteStorageError, match="WebDAV listing"):
        await _strategy().list_files()


async def test_an_unreachable_server_raises_instead_of_looking_like_an_empty_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    _serve(monkeypatch, refuse)

    with pytest.raises(httpx.ConnectError):
        await _strategy().list_files()


_BODY = _MULTISTATUS.encode()


def _limit(monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    monkeypatch.setattr(webdav, "_MAX_LISTING_BYTES", size)


def _chunks(chunk: bytes, consumed: list[int]) -> AsyncIterator[bytes]:
    """An answer that never ends; ``consumed`` gets one entry per chunk the client took."""

    async def body() -> AsyncIterator[bytes]:
        for _ in range(100_000):
            consumed.append(1)
            yield chunk

    return body()


async def test_a_listing_exactly_at_the_size_limit_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _limit(monkeypatch, len(_BODY))
    _serve(monkeypatch, lambda request: httpx.Response(207, content=_BODY))

    files = await _strategy().list_files()

    assert [f.file_name for f in files] == [_NAME]


async def test_a_listing_one_byte_over_the_size_limit_raises_instead_of_looking_shorter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit(monkeypatch, len(_BODY) - 1)
    _serve(monkeypatch, lambda request: httpx.Response(207, content=_BODY))

    with pytest.raises(RemoteStorageError, match="WebDAV listing is larger than"):
        await _strategy().list_files()


async def test_a_listing_that_announces_more_than_the_limit_is_refused_without_reading_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limit(monkeypatch, 4096)
    consumed: list[int] = []
    _serve(
        monkeypatch,
        lambda request: httpx.Response(207, headers={"content-length": "4097"}, content=_chunks(b"x" * 1024, consumed)),
    )

    with pytest.raises(RemoteStorageError, match="WebDAV listing is larger than"):
        await _strategy().list_files()

    assert consumed == []


async def test_a_listing_that_announces_no_length_stops_being_read_at_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _limit(monkeypatch, 4096)
    consumed: list[int] = []
    _serve(monkeypatch, lambda request: httpx.Response(207, content=_chunks(b"x" * 1024, consumed)))

    with pytest.raises(RemoteStorageError, match="WebDAV listing is larger than"):
        await _strategy().list_files()

    assert len(consumed) == 5  # four chunks fill the limit, the fifth proves it is exceeded


async def test_a_server_that_announces_less_than_it_sends_is_still_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    _limit(monkeypatch, 4096)
    consumed: list[int] = []
    _serve(
        monkeypatch,
        lambda request: httpx.Response(207, headers={"content-length": "10"}, content=_chunks(b"x" * 1024, consumed)),
    )

    with pytest.raises(RemoteStorageError, match="WebDAV listing is larger than"):
        await _strategy().list_files()

    assert len(consumed) == 5


async def test_an_announced_length_that_is_not_a_number_does_not_stop_the_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    # 0xB2 is "²": ``str.isdigit`` accepts it, ``int`` does not.
    _serve(monkeypatch, lambda request: httpx.Response(207, headers=[(b"content-length", b"\xb2")], content=_BODY))

    files = await _strategy().list_files()

    assert [f.file_name for f in files] == [_NAME]


def _listing_of_about(size: int) -> bytes:
    """A valid multistatus answer of roughly ``size`` bytes, made of one real-looking entry repeated."""
    entry = _MULTISTATUS.split("</d:response>\n", 1)[1].split("</d:multistatus>")[0]
    head = '<?xml version="1.0" encoding="UTF-8"?>\n<d:multistatus xmlns:d="DAV:">\n'
    return (head + entry * (size // len(entry)) + "</d:multistatus>").encode()


async def test_the_real_size_limit_admits_a_listing_of_tens_of_thousands_of_backups(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _listing_of_about(webdav._MAX_LISTING_BYTES - 4096)
    _serve(monkeypatch, lambda request: httpx.Response(207, content=body))

    files = await _strategy().list_files()

    assert len(files) > 20_000


async def test_the_real_size_limit_refuses_an_answer_a_byte_over_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, lambda request: httpx.Response(207, content=b"x" * (webdav._MAX_LISTING_BYTES + 1)))

    with pytest.raises(RemoteStorageError, match="larger than 8 MiB"):
        await _strategy().list_files()


@pytest.mark.parametrize("charset", ["utf-8", "iso-8859-1"])
async def test_a_listing_is_decoded_with_the_charset_the_server_declares(monkeypatch: pytest.MonkeyPatch, charset: str) -> None:
    name = "myrm.20250101120000.café.1a2b3c4d.linux.json.gz"
    body = _MULTISTATUS.replace(_NAME, name).encode(charset)
    _serve(
        monkeypatch,
        lambda request: httpx.Response(207, headers={"content-type": f"application/xml; charset={charset}"}, content=body),
    )

    files = await _strategy().list_files()

    assert [f.file_name for f in files] == [name]
