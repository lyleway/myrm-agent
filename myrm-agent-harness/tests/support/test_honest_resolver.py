"""A name reserved for tests does not resolve on a machine whose resolver answers every name.

[INPUT]
- core.security.guards.ssrf::async_validate_url_for_ssrf (POS: the guard that refuses a URL whose host does not resolve)
- tests.support.honest_resolver (POS: the plugin under test)

[OUTPUT]
- test_a_reserved_name_does_not_resolve: the suffixes RFC 6761 reserves fail with ``EAI_NONAME``, however they are spelled
- test_every_other_name_is_answered_as_before: nothing else changes
- test_the_ssrf_guard_refuses_a_reserved_name: the effect on the code under test
- test_every_test_runs_inside_the_honest_resolver: the plugin is registered and its fixture wraps the resolver of every test

[POS]
Pins ``_honest_resolver`` against a resolver in fake-IP mode (``_answers_every_name``), the machine on which a test of
``FetchEngine`` against ``blocked.example`` passed without any stub while the CI runner failed it.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from myrm_agent_harness.core.security.guards.ssrf import async_validate_url_for_ssrf
from tests.support import honest_resolver as plugin
from tests.support.honest_resolver import honest_resolver

_ANSWER = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def _answers_every_name(
    host: object, *args: object, **kwargs: object
) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    return _ANSWER


@pytest.fixture
def resolver_in_fake_ip_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _answers_every_name)


@pytest.mark.usefixtures("resolver_in_fake_ip_mode")
@pytest.mark.parametrize(
    "name",
    [
        "blocked.example",
        "a.b.example",
        "host.invalid",
        "svc.test",
        "BLOCKED.EXAMPLE",
        "blocked.example.",
        b"blocked.example",
    ],
)
def test_a_reserved_name_does_not_resolve(name: bytes | str) -> None:
    with honest_resolver(), pytest.raises(socket.gaierror) as caught:
        socket.getaddrinfo(name, 443)

    assert caught.value.errno == socket.EAI_NONAME


@pytest.mark.usefixtures("resolver_in_fake_ip_mode")
@pytest.mark.parametrize(
    "name", ["example.com", "test.example.com", "api.invalid.org", "attest.net", "notexample", "localhost", None]
)
def test_every_other_name_is_answered_as_before(name: str | None) -> None:
    with honest_resolver():
        assert socket.getaddrinfo(name, 443) == _ANSWER


@pytest.mark.usefixtures("resolver_in_fake_ip_mode")
async def test_the_event_loop_lookup_fails_the_same_way() -> None:
    with honest_resolver(), pytest.raises(socket.gaierror):
        await asyncio.get_running_loop().getaddrinfo("blocked.example", 443)


@pytest.mark.usefixtures("resolver_in_fake_ip_mode")
async def test_the_ssrf_guard_refuses_a_reserved_name() -> None:
    with honest_resolver():
        result = await async_validate_url_for_ssrf("https://blocked.example/a")

    assert not result.safe
    assert result.error is not None
    assert "DNS resolution failed" in result.error


@pytest.mark.usefixtures("resolver_in_fake_ip_mode")
def test_a_test_stubs_the_resolver_inside_the_block_and_the_block_restores_it() -> None:
    with honest_resolver():
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(socket, "getaddrinfo", _answers_every_name)
            assert socket.getaddrinfo("blocked.example", 443) == _ANSWER

        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("blocked.example", 443)

    assert socket.getaddrinfo("blocked.example", 443) == _ANSWER


def test_every_test_runs_inside_the_honest_resolver() -> None:
    assert socket.getaddrinfo.__module__ == plugin.__name__
