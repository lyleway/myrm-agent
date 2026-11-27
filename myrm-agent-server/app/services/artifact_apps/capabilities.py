"""Capability vocabulary and scope rules for installed artifact apps.

[INPUT]
- (none)

[OUTPUT]
- Capability / Decision / Outcome: Literal types shared by the API schemas and the store.
- normalize_scope: validates and canonicalizes the scope of one capability.
- normalize_accelerator: the canonical spelling of one global key combination.
- InvalidScopeError: raised for a scope the capability cannot carry.

[POS]
Business layer vocabulary. A scope is what the page declared and the user approved: nothing for ``notify``, ``tray`` and
``float``, a list of exact public host names for ``fetch``, a list of key combinations for ``shortcut``. Exact names only
(no wildcards, no IP literals, no ports) so that the list the user reads in the permission dialog is the list the proxy
enforces; a key combination needs a modifier other than Shift so a page cannot take over plain typing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final, Literal

Capability = Literal["notify", "fetch", "tray", "shortcut", "float"]
Decision = Literal["allow", "deny"]
Outcome = Literal["ok", "denied", "rate_limited", "error"]

MAX_FETCH_HOSTS: Final = 20
MAX_SHORTCUTS: Final = 4

_SCOPELESS: Final = frozenset({"notify", "tray", "float"})
_MODIFIERS: Final = ("CommandOrControl", "Control", "Alt", "Shift")
_KEY: Final = re.compile(r"^(?:[A-Z0-9]|F(?:[1-9]|1[0-2]))$")

# Dotted names ending in an alphabetic label: rejects `localhost`, IPv4 / IPv6 literals and anything with a port.
_HOST_NAME: Final = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class InvalidScopeError(ValueError):
    """The scope is malformed or not allowed for the capability."""


def normalize_accelerator(raw: str) -> str:
    """Return ``Modifier+...+Key`` in canonical order (``CommandOrControl+Shift+K``) or raise ``InvalidScopeError``."""
    parts = [part.strip() for part in raw.split("+")]
    key = parts[-1].upper()
    modifiers = parts[:-1]
    known = {name.lower(): name for name in _MODIFIERS}
    if not _KEY.fullmatch(key) or not modifiers or any(part.lower() not in known for part in modifiers):
        raise InvalidScopeError(f"not a supported key combination: {raw!r}")
    chosen = {known[part.lower()] for part in modifiers}
    if len(chosen) != len(modifiers) or not chosen - {"Shift"}:
        raise InvalidScopeError(f"a key combination needs a modifier other than Shift and no repeats: {raw!r}")
    return "+".join([name for name in _MODIFIERS if name in chosen] + [key])


def normalize_scope(capability: Capability, values: Iterable[str]) -> list[str]:
    """Return the canonical scope: sorted, de-duplicated names (``[]`` for a capability without scope)."""
    given = list(values)
    if capability in _SCOPELESS:
        if given:
            raise InvalidScopeError(f"{capability} takes no scope")
        return []
    if capability == "shortcut":
        combos = {normalize_accelerator(value) for value in given}
        if not combos:
            raise InvalidScopeError("shortcut needs at least one key combination")
        if len(combos) > MAX_SHORTCUTS:
            raise InvalidScopeError(f"shortcut allows at most {MAX_SHORTCUTS} key combinations")
        return sorted(combos)
    names = {value.strip().lower() for value in given}
    if not names:
        raise InvalidScopeError("fetch needs at least one host name")
    if len(names) > MAX_FETCH_HOSTS:
        raise InvalidScopeError(f"fetch allows at most {MAX_FETCH_HOSTS} host names")
    for name in names:
        if not _HOST_NAME.fullmatch(name):
            raise InvalidScopeError(f"not an exact public host name: {name!r}")
    return sorted(names)
