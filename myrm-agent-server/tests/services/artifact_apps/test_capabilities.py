"""Scope rules: what the permission dialog lists is exactly what the proxy enforces."""

from __future__ import annotations

import pytest

from app.services.artifact_apps.capabilities import MAX_FETCH_HOSTS, InvalidScopeError, normalize_scope


def test_fetch_scope_is_sorted_lower_cased_and_deduplicated() -> None:
    scope = normalize_scope("fetch", ["API.Weather.com", "api.weather.com", " a.example.org "])

    assert scope == ["a.example.org", "api.weather.com"]


def test_notify_has_an_empty_scope() -> None:
    assert normalize_scope("notify", []) == []


def test_notify_refuses_a_scope() -> None:
    with pytest.raises(InvalidScopeError):
        normalize_scope("notify", ["api.weather.com"])


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "10.0.0.5", "[::1]", "api.example.com:8443", "*.example.com", "example", "a b.example.com", ""],
)
def test_fetch_refuses_anything_that_is_not_an_exact_public_host_name(host: str) -> None:
    with pytest.raises(InvalidScopeError):
        normalize_scope("fetch", [host])


def test_fetch_needs_at_least_one_host() -> None:
    with pytest.raises(InvalidScopeError):
        normalize_scope("fetch", [])


def test_fetch_caps_the_number_of_hosts() -> None:
    hosts = [f"h{i}.example.com" for i in range(MAX_FETCH_HOSTS + 1)]

    with pytest.raises(InvalidScopeError):
        normalize_scope("fetch", hosts)
    assert len(normalize_scope("fetch", hosts[:MAX_FETCH_HOSTS])) == MAX_FETCH_HOSTS
