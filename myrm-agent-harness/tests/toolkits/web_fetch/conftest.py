"""Shared fixtures for web_fetch tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Self
from unittest.mock import AsyncMock

import curl_cffi.requests
import pytest


@pytest.fixture
def install_fake_curl_session(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Install a minimal curl_cffi.requests.AsyncSession stand-in for HttpFetcher tests.

    The returned mock stands in for ``session.get``: it receives the request URL and kwargs and returns the response.
    The body of that response reaches ``bounded_get`` the way libcurl delivers it, through ``content_callback``.
    """
    mock_get = AsyncMock()

    class _FakeAsyncSession:
        def __init__(self, **_init_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            content_callback: Callable[[bytes], int] | None = None,
            **kwargs: object,
        ) -> object:
            response = await mock_get(url, **kwargs)
            body = getattr(response, "body", None)
            if content_callback is not None and isinstance(body, bytes):
                content_callback(body)
            return response

    monkeypatch.setattr(curl_cffi.requests, "AsyncSession", _FakeAsyncSession)
    return mock_get
