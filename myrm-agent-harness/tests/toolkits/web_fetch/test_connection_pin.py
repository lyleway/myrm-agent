"""The HTTP tier connects only to the addresses a fetch vetted, whatever else the host name resolves to.

Real curl_cffi performs every request here against listeners of this process. A name under ``.invalid`` never resolves
(RFC 6761), so a request to one can reach a listener only through ``CURLOPT_RESOLVE`` built by ``connection_pin``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from curl_cffi.const import CurlOpt
from curl_cffi.requests import AsyncSession, RequestsError
from curl_cffi.requests.exceptions import CertificateVerifyError

from myrm_agent_harness.toolkits.web_fetch.fetchers import bounded_get as bounded_get_module
from myrm_agent_harness.toolkits.web_fetch.fetchers import connection_pin
from myrm_agent_harness.toolkits.web_fetch.fetchers.bounded_get import FetchedResponse, bounded_get
from myrm_agent_harness.toolkits.web_fetch.fetchers.connection_pin import build_pin_curl_options
from tests.support.mcp_testkit.tls_pki import generate_inspection_chain
from tests.support.tls_loopback import PROXY_VARIABLES, https_server

_PINNED_NAME = "pin-target.invalid"


@dataclass(slots=True)
class _Listener:
    """A loopback HTTP listener that records every request head it receives and answers with its own label."""

    label: str
    port: int = 0
    heads: list[str] = field(default_factory=list)

    @property
    def body(self) -> bytes:
        return f"<html><body>{self.label}</body></html>".encode()

    @property
    def host_headers(self) -> list[str]:
        lines = (line for head in self.heads for line in head.split("\r\n"))
        return [line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("host:")]


@asynccontextmanager
async def _listen(label: str, *, host: str = "127.0.0.1", port: int = 0) -> AsyncIterator[_Listener]:
    listener = _Listener(label)

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            listener.heads.append((await reader.readuntil(b"\r\n\r\n")).decode("latin-1"))
            head = f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {len(listener.body)}\r\n"
            writer.write(f"{head}Connection: close\r\n\r\n".encode() + listener.body)
            await writer.drain()
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(answer, host, port)
    listener.port = server.sockets[0].getsockname()[1]
    try:
        yield listener
    finally:
        server.close()
        await server.wait_closed()


async def _get(url: str, addresses: tuple[str, ...] = ()) -> FetchedResponse:
    return await bounded_get(url, {"timeout": 2, "allow_redirects": False}, addresses)


@pytest.fixture(autouse=True)
def _direct_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy configured on the machine running the tests would answer for the loopback names instead."""
    for name in PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
class TestPinnedConnection:
    async def test_a_pinned_name_reaches_the_pinned_address_and_keeps_its_name(self) -> None:
        async with _listen("pinned") as site:
            url = f"http://{_PINNED_NAME}:{site.port}/page"

            response = await _get(url, ("127.0.0.1",))

            assert (response.status, response.body) == (200, site.body)
            assert site.host_headers == [f"{_PINNED_NAME}:{site.port}"]

            # Without the pin the name leads nowhere, so the pin is what made the connection.
            with suppress(RequestsError):
                await _get(url)
            assert len(site.heads) == 1

    async def test_the_pin_holds_for_the_host_and_port_it_was_made_for_only(self) -> None:
        async with _listen("pinned") as site:
            opts = build_pin_curl_options(f"http://{_PINNED_NAME}:{site.port}/", ("127.0.0.1",))
            async with AsyncSession(curl_options=opts) as session:
                ok = await session.get(f"http://{_PINNED_NAME}:{site.port}/", timeout=2)
                assert ok.status_code == 200
                with suppress(RequestsError):
                    await session.get(f"http://other-target.invalid:{site.port}/", timeout=2)
                with suppress(RequestsError):
                    await session.get(f"http://{_PINNED_NAME}:{site.port + 1}/", timeout=2)

            assert site.host_headers == [f"{_PINNED_NAME}:{site.port}"]

    async def test_every_address_is_tried_in_turn(self) -> None:
        """The first address has no listener on this port, so the connection moves on to the next one."""
        async with _listen("second") as site:
            response = await _get(f"http://{_PINNED_NAME}:{site.port}/", ("::1", "127.0.0.1"))

            assert response.body == site.body

    async def test_a_dual_stack_name_is_dialed_at_the_pinned_family_only(self) -> None:
        async with AsyncExitStack() as stack:
            v4 = await stack.enter_async_context(_listen("v4"))
            try:
                v6 = await stack.enter_async_context(_listen("v6", host="::1", port=v4.port))
            except OSError:
                pytest.skip("no IPv6 loopback, or its port is taken")
            url = f"http://localhost:{v4.port}/"

            only_v4 = await _get(url, ("127.0.0.1",))
            only_v6 = await _get(url, ("::1",))

            assert (only_v4.body, only_v6.body) == (v4.body, v6.body)
            assert (len(v4.heads), len(v6.heads)) == (1, 1)

    async def test_concurrent_fetches_keep_their_own_pins(self) -> None:
        async with AsyncExitStack() as stack:
            sites = [await stack.enter_async_context(_listen(f"site-{index}")) for index in range(8)]

            responses = await asyncio.gather(
                *(
                    _get(f"http://target-{index}.invalid:{site.port}/", ("127.0.0.1",))
                    for index, site in enumerate(sites)
                )
            )

            assert [response.body for response in responses] == [site.body for site in sites]
            assert [len(site.heads) for site in sites] == [1] * len(sites)

    async def test_the_certificate_is_checked_against_the_name_in_the_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A client told to dial an address still validates the host the URL names; an IP-literal URL cannot."""
        chain = generate_inspection_chain(tmp_path, server_names=("pin-tls.test",))
        monkeypatch.setenv("CURL_CA_BUNDLE", str(chain.ca_cert))

        with https_server(chain) as base:
            port = base.rsplit(":", 1)[1]

            named = await _get(f"https://pin-tls.test:{port}/", ("127.0.0.1",))
            with pytest.raises(CertificateVerifyError):
                await _get(f"https://127.0.0.1:{port}/")
            with pytest.raises(CertificateVerifyError):
                await _get(f"https://other.test:{port}/", ("127.0.0.1",))

        assert named.status == 200


class TestBuildPinCurlOptions:
    def test_returns_curl_resolve_entry_for_valid_host_and_addresses(self) -> None:
        opts = build_pin_curl_options(f"http://{_PINNED_NAME}:8080/", ("127.0.0.1",))
        assert opts == {CurlOpt.RESOLVE: [f"{_PINNED_NAME}:8080:127.0.0.1"]}

    @pytest.mark.parametrize(
        ("url", "addresses"),
        [("http://127.0.0.1:8080/", ("127.0.0.1",)), ("http://[::1]/", ("::1",)), ("http://example.test/", ())],
        ids=["ipv4-literal", "ipv6-literal", "name-without-addresses"],
    )
    def test_nothing_is_pinned_when_there_is_nothing_to_pin(self, url: str, addresses: tuple[str, ...]) -> None:
        assert build_pin_curl_options(url, addresses) == {}


class TestResolveEntry:
    @pytest.mark.parametrize(
        ("url", "addresses", "entry"),
        [
            ("https://example.test/a?q=1", ("93.184.216.34",), "example.test:443:93.184.216.34"),
            ("http://example.test/", ("93.184.216.34",), "example.test:80:93.184.216.34"),
            (
                "http://example.test:8080/",
                ("93.184.216.34", "2606:2800:220:1::1"),
                "example.test:8080:93.184.216.34,2606:2800:220:1::1",
            ),
            ("https://user:pw@Example.test./x", ("93.184.216.34",), "example.test.:443:93.184.216.34"),
        ],
    )
    def test_the_entry_names_the_host_the_port_and_every_address(
        self, url: str, addresses: tuple[str, ...], entry: str
    ) -> None:
        assert connection_pin._resolve_entry(url, addresses) == entry

    @pytest.mark.parametrize(
        ("url", "addresses"),
        [
            ("https://example.test/", ()),
            ("https://93.184.216.34/", ("93.184.216.34",)),
            ("https://[2606:2800:220:1::1]/", ("2606:2800:220:1::1",)),
            ("https://example.test/", ("fe80::1",)),
            ("https://example.test/", ("93.184.216.34", "fe80::1")),
        ],
        ids=["no-address", "ipv4-literal", "ipv6-literal", "link-local-v6", "link-local-v6-among-others"],
    )
    def test_there_is_no_entry_when_there_is_nothing_to_pin_or_it_cannot_be_written(
        self, url: str, addresses: tuple[str, ...]
    ) -> None:
        assert connection_pin._resolve_entry(url, addresses) is None


@pytest.mark.asyncio
async def test_a_request_through_a_proxy_is_not_pinned(
    install_fake_curl_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proxy resolves the host itself, so the addresses of the fetch's own lookup mean nothing to that request."""
    seen: list[tuple[str, tuple[str, ...]]] = []

    def record(url: str, addresses: tuple[str, ...]) -> dict[object, object]:
        seen.append((url, addresses))
        return {}

    monkeypatch.setattr(bounded_get_module, "build_pin_curl_options", record)
    response = MagicMock(status_code=200, headers={}, url="http://example.test/", encoding="utf-8", body=b"")
    install_fake_curl_session.return_value = response

    await bounded_get("http://example.test/", {"proxy": "http://proxy.test:8080"}, ("93.184.216.34",))
    await bounded_get("http://example.test/", {}, ("93.184.216.34",))

    assert seen == [
        ("http://example.test/", ()),
        ("http://example.test/", ("93.184.216.34",)),
    ]
