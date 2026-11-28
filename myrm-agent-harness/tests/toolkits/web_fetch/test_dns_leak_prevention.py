"""Unit tests for DNS leak prevention in proxy mode (BrowserPool DoH launch args)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from myrm_agent_harness.toolkits.browser.pool import GlobalBrowserPool
from myrm_agent_harness.toolkits.browser.pool.proxy import ProxyConfig, RoundRobinProxyPool

_DOH_FLAG = "--dns-over-https-templates=https://cloudflare-dns.com/dns-query"


@pytest.fixture(autouse=True)
def _disable_ssrf_dns_pin():
    """Keep these tests on DoH argument plumbing without requiring network DNS lookups."""
    with patch(
        "myrm_agent_harness.toolkits.browser.pool.egress_guard.is_ssrf_shield_enabled",
        return_value=False,
    ):
        yield


class TestBrowserPoolDnsProtection:
    """BrowserPool DNS leak prevention via Chrome launch args."""

    def test_doh_flag_added_with_proxy_pool(self) -> None:
        proxy = ProxyConfig(server="http://proxy.example.com:8080")
        pool = RoundRobinProxyPool(proxies=[proxy])
        browser_pool = GlobalBrowserPool(proxy_pool=pool)

        args = browser_pool._launch_options.get("args", [])
        assert _DOH_FLAG in args
        browser_pool._lifecycle_task = None

    def test_doh_flag_absent_without_proxy_pool(self) -> None:
        browser_pool = GlobalBrowserPool(proxy_pool=None)

        args = browser_pool._launch_options.get("args", [])
        assert _DOH_FLAG not in args
        browser_pool._lifecycle_task = None

    def test_doh_flag_not_duplicated(self) -> None:
        proxy = ProxyConfig(server="http://proxy.example.com:8080")
        pool = RoundRobinProxyPool(proxies=[proxy])
        browser_pool = GlobalBrowserPool(proxy_pool=pool)

        args = browser_pool._launch_options.get("args", [])
        assert args.count(_DOH_FLAG) == 1
        browser_pool._lifecycle_task = None

    def test_doh_appends_to_existing_custom_args(self) -> None:
        """When user provides custom launch_options with args, DoH flag appends."""
        proxy = ProxyConfig(server="http://proxy.example.com:8080")
        pool = RoundRobinProxyPool(proxies=[proxy])
        custom_opts: dict[str, object] = {"args": ["--disable-gpu", "--no-sandbox"]}
        browser_pool = GlobalBrowserPool(proxy_pool=pool, launch_options=custom_opts)

        args = browser_pool._launch_options.get("args", [])
        assert "--disable-gpu" in args
        assert "--no-sandbox" in args
        assert _DOH_FLAG in args
        browser_pool._lifecycle_task = None

    def test_doh_creates_args_when_custom_opts_lack_args_key(self) -> None:
        """When user provides launch_options without 'args' key, DoH still injected."""
        proxy = ProxyConfig(server="http://proxy.example.com:8080")
        pool = RoundRobinProxyPool(proxies=[proxy])
        custom_opts: dict[str, object] = {"headless": True}
        browser_pool = GlobalBrowserPool(proxy_pool=pool, launch_options=custom_opts)

        args = browser_pool._launch_options.get("args", [])
        assert _DOH_FLAG in args
        assert browser_pool._launch_options.get("headless") is True
        browser_pool._lifecycle_task = None

    def test_doh_flag_propagates_to_launcher(self) -> None:
        """DoH flag in _launch_options is passed through to BrowserLauncher."""
        from myrm_agent_harness.toolkits.browser.pool.config import BrowserEngine

        proxy = ProxyConfig(server="http://proxy.example.com:8080")
        pool = RoundRobinProxyPool(proxies=[proxy])
        browser_pool = GlobalBrowserPool(proxy_pool=pool)

        launcher = browser_pool._get_launcher(BrowserEngine.CHROMIUM_PATCHRIGHT)
        launcher_args = launcher._launch_options.get("args", [])
        assert _DOH_FLAG in launcher_args
        browser_pool._lifecycle_task = None
