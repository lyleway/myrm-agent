"""The proxy's own contract: the host allowlist, header filtering and the flags it forces on ``secure_request``."""

from __future__ import annotations

import httpx
import pytest
from myrm_agent_harness.core.security.guards.ssrf import SSRFSecurityError
from myrm_agent_harness.core.security.http.secure_fetch import ContentTooLargeError

from app.services.artifact_apps import fetch_proxy
from app.services.artifact_apps.fetch_proxy import (
    MAX_REQUEST_BYTES,
    FetchRejected,
    FetchRequest,
    Method,
    proxy_fetch,
    target_host,
)

_ALLOWED = frozenset({"api.weather.com"})


def _request(
    url: str = "https://api.weather.com/v1/now",
    *,
    method: Method = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> FetchRequest:
    return FetchRequest(url=url, method=method, headers=headers or {}, body=body)


class _SecureRequestSpy:
    """Stands in for ``secure_request`` and records how the proxy called it."""

    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def __call__(self, client: httpx.AsyncClient, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _SecureRequestSpy:
    created = _SecureRequestSpy(httpx.Response(200, content=b'{"temp": 21}', headers={"content-type": "application/json"}))
    monkeypatch.setattr(fetch_proxy, "secure_request", created)
    return created


def test_target_host_accepts_a_plain_https_url_on_the_default_port() -> None:
    assert target_host("https://API.Weather.com/v1/now?q=1") == "api.weather.com"
    assert target_host("https://api.weather.com:443/") == "api.weather.com"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.weather.com/",
        "https://user:pw@api.weather.com/",
        "https://api.weather.com:8443/",
        "https://api.weather.com:notaport/",
        "ftp://api.weather.com/",
        "//api.weather.com/",
        "https://" + "a" * 2100 + ".com/",
    ],
)
def test_target_host_refuses_anything_but_plain_https_on_the_default_port(url: str) -> None:
    with pytest.raises(FetchRejected) as refused:
        target_host(url)

    assert refused.value.code == "bad_request"


async def test_a_host_the_user_did_not_allow_never_reaches_the_network(spy: _SecureRequestSpy) -> None:
    with pytest.raises(FetchRejected) as refused:
        await proxy_fetch(_request("https://evil.example.com/steal"), _ALLOWED)

    assert refused.value.code == "host_not_granted"
    assert spy.calls == []


async def test_a_grant_for_one_host_does_not_cover_its_subdomains_or_parents(spy: _SecureRequestSpy) -> None:
    for url in ("https://sub.api.weather.com/", "https://weather.com/"):
        with pytest.raises(FetchRejected) as refused:
            await proxy_fetch(_request(url), _ALLOWED)
        assert refused.value.code == "host_not_granted"
    assert spy.calls == []


async def test_the_request_runs_with_the_shield_forced_on_and_redirects_refused(spy: _SecureRequestSpy) -> None:
    result = await proxy_fetch(_request(), _ALLOWED)

    (call,) = spy.calls
    assert call["enable_ssrf_shield"] is True
    assert call["allowed_internal_hosts"] == []
    assert call["max_redirects"] == 0
    assert call["max_content_length"] == fetch_proxy.MAX_RESPONSE_BYTES
    assert (result.status, result.body) == (200, '{"temp": 21}')


async def test_headers_that_could_reroute_the_request_are_dropped(spy: _SecureRequestSpy) -> None:
    headers = {
        "Host": "internal-admin",
        "Cookie": "session=1",
        "Origin": "https://evil.example.com",
        "Referer": "https://evil.example.com/",
        "Proxy-Authorization": "Basic x",
        "X-Forwarded-For": "10.0.0.1",
        "Content-Length": "5",
        "Sec-Fetch-Mode": "cors",
        "Accept": "application/json",
        "X-Api-Key": "page-owned-key",
    }

    await proxy_fetch(_request(headers=headers), _ALLOWED)

    assert spy.calls[0]["headers"] == {"Accept": "application/json", "X-Api-Key": "page-owned-key"}


async def test_the_remote_sites_cookies_never_reach_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(200, content=b"ok", headers=[("set-cookie", "a=1"), ("content-type", "text/plain")])
    monkeypatch.setattr(fetch_proxy, "secure_request", _SecureRequestSpy(response))

    result = await proxy_fetch(_request(), _ALLOWED)

    assert "set-cookie" not in result.headers
    assert result.headers["content-type"] == "text/plain"


async def test_a_request_body_over_the_limit_is_refused_before_any_request(spy: _SecureRequestSpy) -> None:
    with pytest.raises(FetchRejected) as refused:
        await proxy_fetch(_request(method="POST", body="x" * (MAX_REQUEST_BYTES + 1)), _ALLOWED)

    assert refused.value.code == "bad_request"
    assert spy.calls == []


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (SSRFSecurityError("Too many redirects (limit: 0)"), "blocked"),
        (ContentTooLargeError("Response body exceeds 1048576 byte limit"), "response_too_large"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("refused"), "upstream_error"),
    ],
)
async def test_a_failed_request_reports_a_stable_code(monkeypatch: pytest.MonkeyPatch, failure: Exception, code: str) -> None:
    monkeypatch.setattr(fetch_proxy, "secure_request", _SecureRequestSpy(failure))

    with pytest.raises(FetchRejected) as refused:
        await proxy_fetch(_request(), _ALLOWED)

    assert refused.value.code == code
