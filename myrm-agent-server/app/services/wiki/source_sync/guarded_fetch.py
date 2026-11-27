"""Guarded GET for the connectors whose address the user types: an RSS feed or a Zotero server.

[INPUT]
- myrm_agent_harness.core.security.http.secure_fetch::{secure_get, ContentTooLargeError} (POS: DNS-pinned GET with per-hop redirect validation and a streamed, size-capped body)
- myrm_agent_harness.core.security.guards.ssrf::SSRFSecurityError (POS: the error a refused address raises)

[OUTPUT]
- SourceFetchError: an address that could not be read, with a message fit to show to the person who configured it
- fetch_configured_url: the response of a GET to such an address

[POS]
A source-sync run fetches an address that whoever saved the configuration chose, publishes what it reads into the wiki
and hands what it fails with back to that person through the state store and the status API. The address is therefore
fetched like every other user-supplied URL: public addresses only in every deployment mode (a host the operator lists
in ``MYRM_ALLOWED_INTERNAL_HOSTS`` is the one exception), each redirect hop checked, the connection made to the address
that was checked, the body read as a capped stream and the whole exchange held to a deadline, since a read timeout
restarts with every byte. A refused address, an unknown host, a refused connection and a timeout fail with one
message, so the text does not tell an internal host that answers from one that does not.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from myrm_agent_harness.core.security.guards.ssrf import SSRFSecurityError
from myrm_agent_harness.core.security.http.secure_fetch import DEFAULT_MAX_REDIRECTS, ContentTooLargeError, secure_get

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 25.0
_DEADLINE_SECONDS = 60.0


class SourceFetchError(Exception):
    """An address that could not be read; the message is fit to show to the person who configured it."""


async def fetch_configured_url(
    url: str,
    *,
    headers: dict[str, str],
    max_bytes: int,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> httpx.Response:
    """GET ``url`` and return the successful response, with a body of at most ``max_bytes``.

    A body past the cap fails and is never cut short, since a feed or a JSON page that stops half way parses as
    something else. ``max_redirects=0`` refuses every redirect, for a request that carries a credential header the
    redirect guard does not know to strip.
    """
    try:
        async with asyncio.timeout(_DEADLINE_SECONDS):
            response = await secure_get(
                url,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                headers=headers,
                max_redirects=max_redirects,
                max_content_length=max_bytes,
            )
    except ContentTooLargeError as exc:
        raise SourceFetchError("the response is too large") from exc
    # TimeoutError is an OSError, as is the socket error of a host that does not resolve.
    except (SSRFSecurityError, httpx.HTTPError, OSError) as exc:
        logger.warning("A configured address could not be read: %s: %s", type(exc).__name__, exc)
        raise SourceFetchError("the address could not be reached") from exc
    if not response.is_success:
        raise SourceFetchError(f"the server answered {response.status_code}")
    return response
