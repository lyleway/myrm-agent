"""Tests for the external L4 endpoint policy, hosted switch, and DNS-pinned validation."""

from __future__ import annotations

import os
import socket
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.config import (
    SearchServiceConfigItem,
    SearchServicesConfigValue,
    WebFetchEscalationConfigValue,
    WebFetchFirecrawlConfig,
)
from app.services.web_fetch.escalation.registry import resolve_firecrawl_api_key
from app.services.web_fetch.providers.endpoint_policy import (
    DEFAULT_FIRECRAWL_API_BASE,
    EndpointPolicyError,
    is_custom_api_base_disabled,
    resolve_and_validate_endpoint_ips,
    validate_firecrawl_endpoint,
)


def test_empty_api_base_returns_default() -> None:
    assert validate_firecrawl_endpoint(None) == DEFAULT_FIRECRAWL_API_BASE
    assert validate_firecrawl_endpoint("   ") == DEFAULT_FIRECRAWL_API_BASE


def test_public_https_endpoint_allowed() -> None:
    assert validate_firecrawl_endpoint("https://firecrawl.example.com/", api_key="fc-1") == "https://firecrawl.example.com"


@pytest.mark.parametrize(
    "bad_url",
    [
        "ftp://firecrawl.example.com",
        "file:///etc/passwd",
        "https://user:pass@firecrawl.example.com",
        "https://token@firecrawl.example.com",
        "https://169.254.169.254",
        "http://169.254.169.254",
        "https://168.63.129.16",
        "https://100.100.100.200",
        "https://[fd00:ec2::254]",
        "https://[::ffff:169.254.169.254]",
        "https://[::ffff:168.63.129.16]",
        "https://metadata.google.internal",
        "https://metadata.tencentyun.com",
        "https://metadata",
        "https://0.0.0.0",
        "https://0",
        "https://[::]",
        "https://224.0.0.1",
    ],
)
def test_always_forbidden_endpoints_rejected_even_with_confirmation(bad_url: str) -> None:
    with pytest.raises(EndpointPolicyError):
        validate_firecrawl_endpoint(bad_url, network_isolation_confirmed=False, api_key="k")
    with pytest.raises(EndpointPolicyError):
        validate_firecrawl_endpoint(bad_url, network_isolation_confirmed=True, api_key="k")


@pytest.mark.parametrize(
    "internal_url",
    [
        "http://firecrawl.example.com",
        "https://localhost:3002",
        "http://127.0.0.1:3002",
        "https://127.1:3002",
        "https://2130706433",
        "https://0x7f000001",
        "https://017700000001",
        "https://[::1]:3002",
        "https://[::ffff:127.0.0.1]:3002",
        "https://10.0.0.5:3002",
        "https://192.168.1.20:3002",
        "https://172.16.0.10:3002",
        "https://100.64.0.5:3002",
        "https://firecrawl.local:3002",
        "https://firecrawl.default.svc:3002",
        "https://firecrawl:3002",
    ],
)
def test_internal_or_http_requires_isolation_confirmation(internal_url: str) -> None:
    with pytest.raises(EndpointPolicyError):
        validate_firecrawl_endpoint(internal_url, network_isolation_confirmed=False, api_key="k")
    assert validate_firecrawl_endpoint(internal_url, network_isolation_confirmed=True, api_key="k") == internal_url


def test_custom_api_base_rejects_inherit_from_search_without_explicit_key() -> None:
    with pytest.raises(EndpointPolicyError, match="inherit"):
        validate_firecrawl_endpoint(
            "https://firecrawl.example.com",
            inherit_from_search=True,
            api_key=None,
        )
    # Explicit key is allowed even if inherit_from_search flag is still true.
    assert (
        validate_firecrawl_endpoint(
            "https://firecrawl.example.com",
            inherit_from_search=True,
            api_key="dedicated-key",
        )
        == "https://firecrawl.example.com"
    )


def test_resolve_firecrawl_api_key_never_inherits_for_custom_api_base() -> None:
    cfg = WebFetchEscalationConfigValue(
        enabled=True,
        firecrawl=WebFetchFirecrawlConfig(
            inherit_from_search=True,
            api_key=None,
            api_base="https://selfhosted.example.com",
        ),
    )
    search = SearchServicesConfigValue(
        searchServiceConfigs=[
            SearchServiceConfigItem(
                id="fc1",
                enabled=True,
                priority=1,
                search_service="firecrawl",
                api_key="fc-secret-search-key",
                createdAt=1,
            )
        ]
    )
    assert resolve_firecrawl_api_key(cfg, search) is None


def test_hosted_operator_switch_disables_custom_api_base() -> None:
    with patch.dict(os.environ, {"MYRM_WEB_FETCH_CUSTOM_API_BASE_DISABLED": "1"}):
        assert is_custom_api_base_disabled() is True
        with pytest.raises(EndpointPolicyError, match="disabled"):
            validate_firecrawl_endpoint("https://selfhosted.example.com", api_key="k")
        # Default official endpoint remains allowed.
        assert validate_firecrawl_endpoint(None) == DEFAULT_FIRECRAWL_API_BASE


@pytest.mark.asyncio
async def test_resolve_and_validate_endpoint_ips_blocks_dns_rebinding_to_metadata_or_private() -> None:
    fake_loop = AsyncMock()
    # 1. Rebinding to Azure WireServer 168.63.129.16 is always blocked.
    fake_loop.getaddrinfo = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("168.63.129.16", 443))])
    with patch("asyncio.get_running_loop", return_value=fake_loop):
        with pytest.raises(EndpointPolicyError, match="forbidden"):
            await resolve_and_validate_endpoint_ips("https://rebind.example.com/v2/scrape", network_isolation_confirmed=True)

    # 2. Rebinding to private IP blocked unless confirmed.
    fake_loop.getaddrinfo = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))])
    with patch("asyncio.get_running_loop", return_value=fake_loop):
        with pytest.raises(EndpointPolicyError, match="private"):
            await resolve_and_validate_endpoint_ips("https://rebind.example.com/v2/scrape", network_isolation_confirmed=False)
        ips = await resolve_and_validate_endpoint_ips("https://rebind.example.com/v2/scrape", network_isolation_confirmed=True)
        assert ips == ["10.0.0.8"]
