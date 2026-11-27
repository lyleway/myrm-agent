"""Scope rules: what the permission dialog lists is exactly what the proxy enforces."""

from __future__ import annotations

import pytest

from app.services.artifact_apps.capabilities import (
    MAX_FETCH_HOSTS,
    MAX_SHORTCUTS,
    InvalidScopeError,
    normalize_accelerator,
    normalize_scope,
)


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


@pytest.mark.parametrize("capability", ["tray", "float"])
def test_tray_and_float_carry_no_scope(capability: str) -> None:
    assert normalize_scope(capability, []) == []  # type: ignore[arg-type]
    with pytest.raises(InvalidScopeError):
        normalize_scope(capability, ["x"])  # type: ignore[arg-type]


def test_shortcut_scope_is_canonical_sorted_and_deduplicated() -> None:
    scope = normalize_scope("shortcut", ["shift+commandorcontrol+k", "CommandOrControl+Shift+K", "alt+f5"])

    assert scope == ["Alt+F5", "CommandOrControl+Shift+K"]


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("commandorcontrol+k", "CommandOrControl+K"),
        (" Control + Alt + 7 ", "Control+Alt+7"),
        ("Shift+Alt+F12", "Alt+Shift+F12"),
    ],
)
def test_accelerators_are_spelled_canonically(raw: str, canonical: str) -> None:
    assert normalize_accelerator(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    ["K", "Shift+K", "Alt", "Alt+", "Alt+KK", "Alt+F13", "Alt+F0", "Super+K", "Cmd+K", "Alt+Alt+K", "Alt+Space", "Alt+é", ""],
)
def test_accelerators_that_could_take_over_typing_or_are_not_supported_are_refused(raw: str) -> None:
    with pytest.raises(InvalidScopeError):
        normalize_accelerator(raw)


def test_shortcut_needs_at_least_one_and_caps_the_number() -> None:
    with pytest.raises(InvalidScopeError):
        normalize_scope("shortcut", [])
    combos = [f"Alt+{key}" for key in "ABCDE"]
    with pytest.raises(InvalidScopeError):
        normalize_scope("shortcut", combos)
    assert len(normalize_scope("shortcut", combos[:MAX_SHORTCUTS])) == MAX_SHORTCUTS
