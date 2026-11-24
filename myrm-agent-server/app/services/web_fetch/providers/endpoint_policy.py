"""External L4 endpoint policy, validation, and DNS-pinned resolution.

[INPUT]
- myrm_agent_harness.utils.url_utils.blocking::SSRF_BLOCKED_HOSTNAMES, is_blocked_ip (POS: SSRF IP & hostname SSOT)

[OUTPUT]
- EndpointPolicyError: Raised when a custom L4 api_base violates endpoint policy.
- is_custom_api_base_disabled: True when hosted operator env disables custom api_base.
- validate_firecrawl_endpoint: Validate Firecrawl api_base against scheme, metadata, userinfo, and key-inheritance rules.
- resolve_and_validate_endpoint_ips: Resolve endpoint host at connect time and verify all IPs against policy.

[POS]
SSOT security gate for custom remote L4 web-fetch endpoints (Firecrawl and compatible self-hosted services).
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit

from myrm_agent_harness.utils.url_utils.blocking import (
    SSRF_BLOCKED_HOSTNAMES,
    is_blocked_ip,
)

DEFAULT_FIRECRAWL_API_BASE = "https://api.firecrawl.dev"

_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "denied", "on"})

# Cloud metadata IPs that are NEVER allowed even when network_isolation_confirmed=True:
# - AWS / GCP / Azure IMDS: 169.254.169.254, fd00:ec2::254
# - Azure WireServer (public range): 168.63.129.16
# - Alibaba Cloud IMDS (CGNAT range): 100.100.100.200
_ALWAYS_FORBIDDEN_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("168.63.129.16"),
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)

_ALWAYS_FORBIDDEN_HOSTNAMES = SSRF_BLOCKED_HOSTNAMES - frozenset({"localhost"})

_INTERNAL_HOST_SUFFIXES = (
    ".local",
    ".localdomain",
    ".home.arpa",
    ".svc",
    ".cluster.local",
    ".internal",
    ".lan",
)


class EndpointPolicyError(ValueError):
    """Raised when a custom L4 endpoint violates security policy."""


def is_custom_api_base_disabled() -> bool:
    """Return True when the hosted operator disabled custom L4 api_base endpoints."""
    raw = os.getenv("MYRM_WEB_FETCH_CUSTOM_API_BASE_DISABLED", "").strip().lower()
    return raw in _TRUTHY_ENV_VALUES


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse standard and non-standard IPv4/IPv6 literals (decimal, hex, octal, short forms)."""
    cleaned = host.strip().rstrip(".")
    if not cleaned:
        return None
    if cleaned.startswith("[") and cleaned.endswith("]"):
        cleaned = cleaned[1:-1]
    try:
        return ipaddress.ip_address(cleaned)
    except ValueError:
        pass
    # Handle non-standard IPv4 forms accepted by OS resolvers (e.g. 0, 127.1, 2130706433, 0x7f000001, 017700000001).
    try:
        packed = socket.inet_aton(cleaned)
        return ipaddress.IPv4Address(packed)
    except OSError:
        return None


def _is_always_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True for metadata, link-local, unspecified, multicast, or reserved IPs."""
    v4 = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
    check_targets = (ip, v4) if v4 is not None else (ip,)
    for candidate in check_targets:
        if candidate in _ALWAYS_FORBIDDEN_IPS:
            return True
        if candidate.is_link_local or candidate.is_unspecified or candidate.is_multicast:
            return True
        # Python marks IPv6 ::1 as both is_loopback and is_reserved (inside ::/8);
        # loopback is allowed when network_isolation_confirmed=True.
        if candidate.is_reserved and not candidate.is_loopback:
            return True
    return False


def _is_internal_hostname_literal(host: str) -> bool:
    cleaned = host.lower().rstrip(".")
    if cleaned == "localhost" or cleaned.endswith(".localhost"):
        return True
    if any(cleaned.endswith(suffix) for suffix in _INTERNAL_HOST_SUFFIXES):
        return True
    # Single-label hostnames (e.g. "firecrawl", "internal-svc") resolve on local/container networks.
    return "." not in cleaned


def validate_firecrawl_endpoint(
    api_base: str | None,
    *,
    network_isolation_confirmed: bool = False,
    inherit_from_search: bool = False,
    api_key: str | None = None,
) -> str:
    """Validate and normalize a Firecrawl api_base URL.

    Returns ``DEFAULT_FIRECRAWL_API_BASE`` when ``api_base`` is empty/None.
    Raises ``EndpointPolicyError`` on any policy violation.
    """
    raw = (api_base or "").strip().rstrip("/")
    if not raw:
        return DEFAULT_FIRECRAWL_API_BASE

    if is_custom_api_base_disabled():
        raise EndpointPolicyError("Custom remote fetch endpoints are disabled by the host administrator")

    if inherit_from_search and not (api_key or "").strip():
        raise EndpointPolicyError(
            "Custom api_base cannot inherit the search-service Firecrawl API key; provide a dedicated API key"
        )

    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme not in ("https", "http"):
        raise EndpointPolicyError("Custom api_base must use https:// (or http:// with network isolation confirmed)")

    if parsed.username is not None or parsed.password is not None:
        raise EndpointPolicyError("Custom api_base must not include embedded credentials (user:pass@)")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise EndpointPolicyError("Custom api_base must include a valid hostname")

    if host in _ALWAYS_FORBIDDEN_HOSTNAMES:
        raise EndpointPolicyError(f"Custom api_base host '{host}' is forbidden (cloud metadata endpoint)")

    ip = _parse_ip_literal(host)
    if ip is not None:
        if _is_always_forbidden_ip(ip):
            raise EndpointPolicyError(f"Custom api_base IP '{ip}' is forbidden (metadata or special-use address)")
        if is_blocked_ip(ip) and not network_isolation_confirmed:
            raise EndpointPolicyError(
                "Private or loopback api_base requires confirming network isolation from internal/metadata networks"
            )
    elif _is_internal_hostname_literal(host) and not network_isolation_confirmed:
        raise EndpointPolicyError(
            "Internal or localhost api_base requires confirming network isolation from internal/metadata networks"
        )

    if scheme == "http" and not network_isolation_confirmed:
        raise EndpointPolicyError("Plain http:// api_base requires confirming network isolation")

    return raw


async def resolve_and_validate_endpoint_ips(
    url: str,
    *,
    network_isolation_confirmed: bool = False,
) -> list[str]:
    """Resolve the endpoint host at connect time and validate every returned IP.

    Always rejects cloud metadata, link-local, unspecified, multicast, and reserved IPs.
    Private/loopback/CGNAT IPs require ``network_isolation_confirmed=True``.
    """
    parsed = urlsplit(url)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    if not host:
        raise EndpointPolicyError("Endpoint URL missing hostname")
    if host in _ALWAYS_FORBIDDEN_HOSTNAMES:
        raise EndpointPolicyError(f"Endpoint host '{host}' is forbidden")

    ip_literal = _parse_ip_literal(host)
    if ip_literal is not None:
        resolved_ips = [ip_literal]
    else:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise EndpointPolicyError(f"Failed to resolve endpoint host '{host}': {exc}") from exc
        resolved_ips = []
        seen: set[str] = set()
        for info in infos:
            addr_str = str(info[4][0])
            if addr_str in seen:
                continue
            seen.add(addr_str)
            resolved_ips.append(ipaddress.ip_address(addr_str))
        if not resolved_ips:
            raise EndpointPolicyError(f"Endpoint host '{host}' resolved to no addresses")

    validated: list[str] = []
    for ip in resolved_ips:
        if _is_always_forbidden_ip(ip):
            raise EndpointPolicyError(f"Endpoint '{host}' resolved to forbidden address {ip}")
        if is_blocked_ip(ip) and not network_isolation_confirmed:
            raise EndpointPolicyError(f"Endpoint '{host}' resolved to private/loopback address {ip} without confirmation")
        validated.append(str(ip))

    return validated
