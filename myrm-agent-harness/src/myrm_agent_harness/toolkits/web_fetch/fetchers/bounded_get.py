"""A bounded GET through curl_cffi whose body is held to a size bound while it is received.

[INPUT]
- .connection_pin::build_pin_curl_options (POS: builds session-level CURLOPT_RESOLVE options for vetted addresses)
- toolkits.web_fetch.processing.binary_router::MAX_BINARY_SIZE (POS: the largest file body the router parses)
- curl_cffi.curl::CURL_WRITEFUNC_ERROR (POS: the write-callback result that ends a transfer)
- curl_cffi.requests::AsyncSession, RequestsError (POS: direct curl_cffi HTTP client with TLS impersonation)

[OUTPUT]
- MAX_TRANSFER_BYTES: the most decoded body bytes a fetch holds without being over the bound
- RequestParams: typed request parameter dictionary for bounded_get
- FetchedResponse: status, headers, final URL, encoding hint and the kept body of one GET
- bounded_get: direct curl_cffi AsyncSession.get keeping at most MAX_TRANSFER_BYTES + 1 body bytes

[POS]
L1 transfer bound. libcurl hands each decoded chunk to a sink that keeps the first MAX_TRANSFER_BYTES + 1 bytes
and ends the transfer, preventing memory and event-loop exhaustion on oversized or hostile responses.
"""

from __future__ import annotations

import http.cookiejar
import io
from dataclasses import dataclass
from typing import TypedDict

from myrm_agent_harness.toolkits.web_fetch.fetchers.connection_pin import build_pin_curl_options
from myrm_agent_harness.toolkits.web_fetch.processing.binary_router import MAX_BINARY_SIZE

MAX_TRANSFER_BYTES = MAX_BINARY_SIZE
_KEPT_BYTES = MAX_TRANSFER_BYTES + 1
_DEFAULT_IMPERSONATE = "chrome"
_DEFAULT_REFERER = "https://www.google.com/"


class RequestParams(TypedDict, total=False):
    timeout: int | float
    allow_redirects: bool
    headers: dict[str, str]
    proxy: str
    cookies: http.cookiejar.CookieJar | dict[str, str]
    verify: bool | str


@dataclass(frozen=True, slots=True)
class FetchedResponse:
    """What one GET returned: the response line and headers, and the body up to the transfer bound."""

    status: int
    headers: dict[str, str]
    url: str
    encoding: str | None
    body: bytes


class _BodySink:
    """libcurl write callback: keeps the first ``_KEPT_BYTES`` bytes and ends the transfer once they are full."""

    __slots__ = ("_abort", "_buffer", "_size", "capped")

    def __init__(self, abort: int) -> None:
        self._abort = abort
        self._buffer = io.BytesIO()
        self._size = 0
        self.capped = False

    def __call__(self, chunk: bytes) -> int:
        self._size += self._buffer.write(chunk[: _KEPT_BYTES - self._size])
        if self._size < _KEPT_BYTES:
            return len(chunk)
        self.capped = True
        return self._abort

    @property
    def body(self) -> bytes:
        return self._buffer.getvalue()


async def bounded_get(url: str, request: RequestParams, addresses: tuple[str, ...] = ()) -> FetchedResponse:
    """GET ``url`` directly via curl_cffi ``AsyncSession``, keeping at most ``MAX_TRANSFER_BYTES + 1`` body bytes.

    With ``addresses`` the session is created with ``CURLOPT_RESOLVE`` so libcurl connects only to them while
    ``url`` keeps its host name for TLS SNI/certificate verification. Requests through an explicit proxy are not
    pinned because the proxy resolves the host itself.
    """
    from curl_cffi.curl import CURL_WRITEFUNC_ERROR
    from curl_cffi.requests import AsyncSession, RequestsError

    sink = _BodySink(CURL_WRITEFUNC_ERROR)
    pinned = () if request.get("proxy") else addresses
    curl_options = build_pin_curl_options(url, pinned)

    headers = dict(request.get("headers") or {})
    if not any(k.lower() == "referer" for k in headers):
        headers["Referer"] = _DEFAULT_REFERER

    get_kwargs: dict[str, object] = {
        "content_callback": sink,
        "timeout": request.get("timeout", 10),
        "allow_redirects": request.get("allow_redirects", False),
        "headers": headers,
        "verify": request.get("verify", True),
    }
    if request.get("proxy") is not None:
        get_kwargs["proxy"] = request["proxy"]
    if request.get("cookies") is not None:
        get_kwargs["cookies"] = request["cookies"]

    try:
        async with AsyncSession(
            impersonate=_DEFAULT_IMPERSONATE,
            curl_options=curl_options or None,
        ) as session:
            response = await session.get(url, **get_kwargs)
    except RequestsError as exc:
        interrupted = exc.response
        if not sink.capped or interrupted is None:
            raise
        return FetchedResponse(
            interrupted.status_code,
            dict(interrupted.headers),
            interrupted.url,
            interrupted.encoding,
            sink.body,
        )
    return FetchedResponse(
        response.status_code,
        dict(response.headers),
        response.url,
        response.encoding,
        sink.body,
    )
