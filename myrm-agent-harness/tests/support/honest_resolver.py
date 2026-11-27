"""Make the lookup of a name reserved for tests fail the way the real Internet fails it (a pytest plugin).

[INPUT]
- socket::getaddrinfo (POS: the resolver every Python lookup goes through, the one of the event loop included)

[OUTPUT]
- honest_resolver: context manager; ``getaddrinfo`` raises ``gaierror(EAI_NONAME)`` for a name under ``.example``,
  ``.invalid`` or ``.test`` and answers every other name as it did before
- _honest_resolver: autouse fixture; every test runs inside ``honest_resolver``

[POS]
Registered from ``tests/conftest.py`` (``pytest_plugins``). RFC 6761 reserves these suffixes for tests and
documentation, so no honest resolver answers them. A resolver that does (a VPN or proxy in fake-IP mode, a captive
portal) lets a test that never stubbed DNS pass on that machine and fail on a runner whose resolver is honest:
``FetchEngine.crawl`` refuses a URL whose name does not resolve, so the failure reasons it records for
``blocked.example`` existed only where the name was answered. With this plugin every machine fails the lookup the same
way. A test that needs the name to resolve stubs the resolver itself (its patch is applied inside this one and wins).
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

_RESERVED_SUFFIXES = (".example", ".invalid", ".test")


@contextmanager
def honest_resolver() -> Iterator[None]:
    resolve: Callable[..., object] = socket.getaddrinfo

    def getaddrinfo(host: bytes | str | None, *args: object, **kwargs: object) -> object:
        name = host.decode("ascii", "replace") if isinstance(host, bytes) else host
        if name is not None and name.rstrip(".").lower().endswith(_RESERVED_SUFFIXES):
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return resolve(host, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "getaddrinfo", getaddrinfo)
        yield


@pytest.fixture(autouse=True)
def _honest_resolver() -> Iterator[None]:
    with honest_resolver():
        yield
