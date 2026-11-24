"""Connection pinning for the L1 tier: libcurl dials only the addresses a fetch vetted.

[INPUT]
- curl_cffi.const::CurlOpt (POS: libcurl option ids; RESOLVE overrides name resolution for one host and port)
- core.security.guards.ssrf::SSRFSecurityError (POS: the terminal verdict a fetch ends with)

[OUTPUT]
- build_pin_curl_options(): returns session-level ``curl_options`` pinning ``url``'s host to vetted ``addresses``
- ConnectionPinError: raised when connection pinning cannot be enforced

[POS]
L1 connection layer. Passes vetted addresses directly to curl_cffi's ``AsyncSession(curl_options=...)`` via
``CURLOPT_RESOLVE`` so libcurl connects only to those addresses without re-resolving DNS while preserving the
host name in the URL for TLS SNI and certificate verification.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from myrm_agent_harness.core.security.guards.ssrf import SSRFSecurityError

if TYPE_CHECKING:
    from curl_cffi.const import CurlOpt

type CurlOptions = dict[CurlOpt, str | list[str]]


class ConnectionPinError(SSRFSecurityError):
    """The HTTP client does not connect to the addresses it was pinned to, so a fetch must not rely on the pin."""


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _needs_interface(address: str) -> bool:
    """A link-local IPv6 address only means something together with the interface it was found on."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed.version == 6 and parsed.is_link_local


def _resolve_entry(url: str, addresses: tuple[str, ...]) -> str | None:
    """Build the libcurl ``host:port:address,address`` entry for ``CURLOPT_RESOLVE``.

    Returns ``None`` when there is nothing to pin: no addresses, an IP literal host, or a link-local IPv6 address
    that requires an interface scope.
    """
    parts = urlsplit(url)
    host = parts.hostname
    if not addresses or not host or _is_ip_literal(host) or any(_needs_interface(a) for a in addresses):
        return None
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{host}:{port}:{','.join(addresses)}"


def build_pin_curl_options(url: str, addresses: tuple[str, ...]) -> CurlOptions:
    """Return ``curl_options`` pinning ``url``'s host to ``addresses`` for a direct curl_cffi ``AsyncSession``."""
    entry = _resolve_entry(url, addresses)
    if entry is None:
        return {}
    from curl_cffi.const import CurlOpt

    return {CurlOpt.RESOLVE: [entry]}
