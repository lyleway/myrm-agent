"""Host-side network access for sandboxed artifact pages.

[INPUT]
- myrm_agent_harness.core.security.http.secure_fetch::secure_request (POS: SSRF-safe request primitive)
- app.services.artifact_apps.rate_limit::FETCH_LIMITER (POS: Burst bound)

[OUTPUT]
- FetchRequest / FetchResult: the proxy's input and output.
- FetchRejected: raised with a stable ``code`` when the request is refused or fails.
- proxy_fetch: validate against the granted hosts, then run the request.

[POS]
Business layer. A page runs in an opaque-origin sandbox with ``connect-src 'none'`` and cannot reach the network;
when the user granted ``fetch`` for named hosts, the host sends the request on its behalf. This module adds what
``secure_request`` does not know: the exact-host allowlist (redirects are refused, otherwise they would leave it),
request headers that could re-route the request, and a method / size / time limit. Private and loopback targets, DNS
rebinding, response size and per-hop pinning are ``secure_request``'s job; it is called with the shield forced on and
no internal hosts, so neither the environment nor the deploy mode can open an internal address to a page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import urlsplit

import httpx
from myrm_agent_harness.core.security.guards.ssrf import SSRFSecurityError
from myrm_agent_harness.core.security.http.secure_fetch import ContentTooLargeError, secure_request
from myrm_agent_harness.infra.tls_compat import create_httpx_client

Method = Literal["GET", "HEAD", "POST"]

MAX_URL_LENGTH: Final = 2048
MAX_REQUEST_BYTES: Final = 256 * 1024
MAX_RESPONSE_BYTES: Final = 1024 * 1024
TIMEOUT_S: Final = 20.0

# `Host` decides which backend a reverse proxy routes to, so letting a page set it would void the host allowlist.
_FORBIDDEN_REQUEST_HEADERS: Final = frozenset(
    {"host", "connection", "content-length", "transfer-encoding", "upgrade", "origin", "referer", "cookie", "te", "trailer"}
)
_FORBIDDEN_HEADER_PREFIXES: Final = ("proxy-", "x-forwarded-", "sec-")
_UNFORWARDED_RESPONSE_HEADERS: Final = frozenset({"set-cookie", "set-cookie2", "connection", "keep-alive", "transfer-encoding"})


class FetchRejected(Exception):
    """The request was refused or failed; ``code`` is stable and safe to show to the page."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FetchRequest:
    url: str
    method: Method
    headers: dict[str, str]
    body: str | None


@dataclass(frozen=True, slots=True)
class FetchResult:
    status: int
    headers: dict[str, str]
    body: str


def target_host(url: str) -> str:
    """The lower-cased host name of an https URL with no credentials and no non-default port, else ``FetchRejected``."""
    if len(url) > MAX_URL_LENGTH:
        raise FetchRejected("bad_request", "URL is too long")
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:
        raise FetchRejected("bad_request", "URL has an invalid port") from exc
    if parts.scheme != "https" or not parts.hostname or parts.username is not None or port not in (None, 443):
        raise FetchRejected("bad_request", "Only plain https URLs on the default port are allowed")
    return parts.hostname.lower()


def _request_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _FORBIDDEN_REQUEST_HEADERS and not name.lower().startswith(_FORBIDDEN_HEADER_PREFIXES)
    }


def _response_headers(response: httpx.Response) -> dict[str, str]:
    return {name: value for name, value in response.headers.items() if name.lower() not in _UNFORWARDED_RESPONSE_HEADERS}


async def proxy_fetch(request: FetchRequest, allowed_hosts: frozenset[str]) -> FetchResult:
    host = target_host(request.url)
    if host not in allowed_hosts:
        raise FetchRejected("host_not_granted", f"{host} is not among the hosts the user allowed")
    content = request.body.encode("utf-8") if request.body else None
    if content is not None and len(content) > MAX_REQUEST_BYTES:
        raise FetchRejected("bad_request", "Request body is too large")
    async with create_httpx_client(timeout=TIMEOUT_S, follow_redirects=False) as client:
        try:
            response = await secure_request(
                client,
                request.method,
                request.url,
                headers=_request_headers(request.headers),
                content=content,
                timeout=TIMEOUT_S,
                allowed_internal_hosts=[],
                enable_ssrf_shield=True,
                max_redirects=0,
                max_content_length=MAX_RESPONSE_BYTES,
            )
        except SSRFSecurityError as exc:
            raise FetchRejected("blocked", str(exc)) from exc
        except ContentTooLargeError as exc:
            raise FetchRejected("response_too_large", str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise FetchRejected("timeout", "The request timed out") from exc
        except httpx.HTTPError as exc:
            raise FetchRejected("upstream_error", f"The request failed: {type(exc).__name__}") from exc
    return FetchResult(
        status=response.status_code,
        headers=_response_headers(response),
        body=response.content.decode("utf-8", errors="replace"),
    )
