"""The fetch of a configured address against real loopback servers: what it refuses, how far it follows, what it says."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import pytest
from myrm_agent_harness.core.security.http.secure_fetch import DEFAULT_MAX_REDIRECTS

from app.services.wiki.source_sync import guarded_fetch
from app.services.wiki.source_sync.guarded_fetch import SourceFetchError, fetch_configured_url
from tests.support.loopback_origin import LoopbackOrigin, drip, endless, page, redirect

_UNREACHABLE = "the address could not be reached"
_HEADERS = {"User-Agent": "test"}
_FEED = '<rss version="2.0"><channel><item><title>T</title></item></channel></rss>'


@pytest.fixture
def origin() -> Iterator[LoopbackOrigin]:
    server = LoopbackOrigin()
    yield server
    server.close()


@pytest.fixture
def operator_allows_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")


@pytest.fixture(autouse=True)
def _shield_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYRM_ENABLE_SSRF_SHIELD", "true")
    monkeypatch.delenv("MYRM_ALLOWED_INTERNAL_HOSTS", raising=False)


async def _failure(url: str, *, max_bytes: int = 1024 * 1024, max_redirects: int = DEFAULT_MAX_REDIRECTS) -> str:
    with pytest.raises(SourceFetchError) as raised:
        await fetch_configured_url(url, headers=_HEADERS, max_bytes=max_bytes, max_redirects=max_redirects)
    return str(raised.value)


async def test_a_host_the_operator_allows_is_read(origin: LoopbackOrigin, operator_allows_loopback: None) -> None:
    origin.routes["/feed"] = page(_FEED, "application/rss+xml")

    response = await fetch_configured_url(origin.url("/feed"), headers=_HEADERS, max_bytes=1024)

    assert response.text == _FEED
    assert origin.hits == ["/feed"]


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost", "127.1", "2130706433", "0x7f000001", "[::ffff:127.0.0.1]"],
)
async def test_an_internal_address_is_refused_before_any_request(origin: LoopbackOrigin, host: str) -> None:
    origin.routes["/secret"] = page(_FEED)

    message = await _failure(origin.url("/secret", host=host))

    assert message == _UNREACHABLE
    assert origin.hits == []


@pytest.mark.parametrize(
    "url", ["http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/", "http://[::1]/", "ftp://example.com/feed"]
)
async def test_a_metadata_private_or_foreign_scheme_address_is_refused(url: str) -> None:
    assert await _failure(url) == _UNREACHABLE


async def test_a_refused_address_and_a_dead_one_fail_with_the_same_text(
    origin: LoopbackOrigin, monkeypatch: pytest.MonkeyPatch
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    alive = origin.url("/feed")
    dead = f"http://127.0.0.1:{dead_port}/feed"
    origin.routes["/feed"] = page(_FEED)

    refused = [await _failure(alive), await _failure(dead)]
    monkeypatch.setenv("MYRM_ALLOWED_INTERNAL_HOSTS", "127.0.0.1")
    dead_but_allowed = await _failure(dead)

    assert refused == [_UNREACHABLE, _UNREACHABLE]
    assert dead_but_allowed == _UNREACHABLE
    assert origin.hits == []


async def test_a_redirect_to_a_host_the_operator_did_not_allow_is_refused(
    origin: LoopbackOrigin, operator_allows_loopback: None
) -> None:
    origin.routes["/start"] = redirect(origin.url("/secret", host="[::ffff:127.0.0.1]"))
    origin.routes["/secret"] = page(_FEED)

    assert await _failure(origin.url("/start")) == _UNREACHABLE
    assert origin.hits == ["/start"]


async def test_redirects_are_followed_up_to_the_limit(origin: LoopbackOrigin, operator_allows_loopback: None) -> None:
    for index in range(3):
        origin.routes[f"/r{index}"] = redirect(origin.url(f"/r{index + 1}"))
    origin.routes["/r3"] = page(_FEED)

    response = await fetch_configured_url(origin.url("/r0"), headers=_HEADERS, max_bytes=1024, max_redirects=3)

    assert response.text == _FEED
    assert await _failure(origin.url("/r0"), max_redirects=2) == _UNREACHABLE


async def test_no_redirect_is_followed_when_none_is_allowed(origin: LoopbackOrigin, operator_allows_loopback: None) -> None:
    origin.routes["/start"] = redirect(origin.url("/feed"))
    origin.routes["/feed"] = page(_FEED)

    assert await _failure(origin.url("/start"), max_redirects=0) == _UNREACHABLE
    assert origin.hits == ["/start"]


async def test_a_body_past_the_cap_is_an_error_and_never_cut_short(
    origin: LoopbackOrigin, operator_allows_loopback: None
) -> None:
    origin.routes["/big"] = page("x" * 2048)
    origin.routes["/endless"] = endless("x")

    assert await _failure(origin.url("/big"), max_bytes=1024) == "the response is too large"
    assert await _failure(origin.url("/endless"), max_bytes=1024) == "the response is too large"
    response = await fetch_configured_url(origin.url("/big"), headers=_HEADERS, max_bytes=2048)
    assert len(response.text) == 2048


async def test_a_page_that_drips_bytes_is_cut_by_the_deadline(
    origin: LoopbackOrigin, operator_allows_loopback: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guarded_fetch, "_DEADLINE_SECONDS", 0.5)
    origin.routes["/drip"] = drip("x")

    started = time.monotonic()
    message = await _failure(origin.url("/drip"))

    assert message == _UNREACHABLE
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("status", [302, 404, 500])
async def test_an_error_status_names_the_status(origin: LoopbackOrigin, operator_allows_loopback: None, status: int) -> None:
    origin.routes["/feed"] = page("no", status=status)

    assert await _failure(origin.url("/feed")) == f"the server answered {status}"
