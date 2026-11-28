"""A web_fetch request the SSRF policy rejects must end there, and local mode must keep its own machine reachable.

Real loopback servers count every request that reaches them and real curl_cffi performs the HTTP tier,
so each scenario proves what was (or was never) contacted.
"""

from __future__ import annotations

import pytest

from myrm_agent_harness.core.security.guards.ssrf import SSRFResult, SSRFSecurityError
from myrm_agent_harness.toolkits.web_fetch.engine import FetchEngine
from myrm_agent_harness.toolkits.web_fetch.fetchers.http_fetcher import HttpFetcher
from myrm_agent_harness.toolkits.web_fetch.processing.url_normalizer import normalize_url
from tests.toolkits.browser._redirect_servers import page, redirect, serve

pytestmark = pytest.mark.filterwarnings("ignore:The 'strip_cdata' option")

_ARTICLE = "<article><p>" + "reachable-body " * 40 + "</p></article>"


def _engine(*, allow_private_networks: bool) -> FetchEngine:
    return FetchEngine(allow_private_networks=allow_private_networks)


@pytest.mark.asyncio
class TestLocalMode:
    async def test_requested_private_page_is_fetched_by_the_http_tier(self) -> None:
        engine = _engine(allow_private_networks=True)
        with serve({"/page": page(_ARTICLE)}) as site:
            doc = await engine.crawl(site.url("/page"), allow_escalation=False)

        assert doc is not None
        assert "reachable-body" in doc.page_content
        assert site.paths() == ["/page"]

    async def test_redirect_within_the_requested_origin_is_followed(self) -> None:
        engine = _engine(allow_private_networks=True)
        with serve({"/start": redirect("/page"), "/page": page(_ARTICLE)}) as site:
            doc = await engine.crawl(site.url("/start"), allow_escalation=False)

        assert doc is not None
        assert site.paths() == ["/start", "/page"]

    @pytest.mark.parametrize("host", ["127.0.0.1", "2130706433"], ids=["dotted", "decimal-integer"])
    async def test_redirect_to_another_private_origin_is_never_requested(self, host: str) -> None:
        engine = _engine(allow_private_networks=True)
        with serve({"/secret": page(_ARTICLE)}) as internal:
            port = internal.base_url.rsplit(":", 1)[1]
            with serve({"/start": redirect(f"http://{host}:{port}/secret")}) as entry:
                doc = await engine.crawl(entry.url("/start"), allow_escalation=False)

        assert doc is None
        assert internal.hits == []
        assert entry.paths() == ["/start"]

    @pytest.mark.parametrize(
        "metadata_url",
        ["http://169.254.169.254/latest/meta-data/", "http://metadata.google.internal/computeMetadata/v1/"],
    )
    async def test_cloud_metadata_is_blocked_even_when_requested(self, metadata_url: str) -> None:
        engine = _engine(allow_private_networks=True)

        assert await engine.crawl(metadata_url, allow_escalation=False) is None

    async def test_blocked_url_is_remembered_as_failed(self) -> None:
        engine = _engine(allow_private_networks=True)
        metadata_url = "http://169.254.169.254/latest/meta-data/"

        await engine.crawl(metadata_url, allow_escalation=False)

        assert engine._fail_cache.contains(normalize_url(metadata_url))


@pytest.mark.asyncio
class TestCloudMode:
    async def test_blocked_redirect_hop_terminates_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _allow(_url: str) -> SSRFResult:
            return SSRFResult(safe=True)

        monkeypatch.setattr("myrm_agent_harness.core.security.guards.ssrf.async_validate_url_for_ssrf", _allow)
        monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")
        engine = _engine(allow_private_networks=False)
        with serve({"/secret": page(_ARTICLE)}) as internal:
            port = internal.base_url.rsplit(":", 1)[1]
            with serve({"/start": redirect(f"http://localhost:{port}/secret")}) as entry:
                doc = await engine.crawl(entry.url("/start"), allow_escalation=False)

        assert doc is None
        assert entry.paths() == ["/start"]
        assert internal.hits == []


@pytest.mark.asyncio
class TestHttpFetcherPropagation:
    async def test_shield_off_follows_private_redirect_hops(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MYRM_ENABLE_SSRF_SHIELD", "false")
        with serve({"/secret": page(_ARTICLE)}) as internal:
            port = internal.base_url.rsplit(":", 1)[1]
            with serve({"/start": redirect(f"http://127.0.0.1:{port}/secret")}) as entry:
                result = await HttpFetcher(allow_private_networks=True).fetch(entry.url("/start"))

        assert result is not None
        assert internal.paths() == ["/secret"]

    async def test_rejected_hop_raises_instead_of_returning_none(self) -> None:
        with serve({"/secret": page(_ARTICLE)}) as internal:
            port = internal.base_url.rsplit(":", 1)[1]
            with (
                serve({"/start": redirect(f"http://127.0.0.1:{port}/secret")}) as entry,
                pytest.raises(SSRFSecurityError),
            ):
                await HttpFetcher(allow_private_networks=True).fetch(entry.url("/start"))

        assert internal.hits == []
