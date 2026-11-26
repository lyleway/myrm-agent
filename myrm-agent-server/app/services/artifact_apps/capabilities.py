"""Capability vocabulary and scope rules for installed artifact apps.

[INPUT]
- (none)

[OUTPUT]
- Capability / Decision / Outcome: Literal types shared by the API schemas and the store.
- normalize_scope: validates and canonicalizes the scope of one capability.
- InvalidScopeError: raised for a scope the capability cannot carry.

[POS]
Business layer vocabulary. A scope is what the page declared and the user approved: nothing for ``notify``, a list of
exact public host names for ``fetch``. Exact names only (no wildcards, no IP literals, no ports) so that the list the
user reads in the permission dialog is the list the proxy enforces.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final, Literal

Capability = Literal["notify", "fetch"]
Decision = Literal["allow", "deny"]
Outcome = Literal["ok", "denied", "rate_limited", "error"]

MAX_FETCH_HOSTS: Final = 20

# Dotted names ending in an alphabetic label: rejects `localhost`, IPv4 / IPv6 literals and anything with a port.
_HOST_NAME: Final = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class InvalidScopeError(ValueError):
    """The scope is malformed or not allowed for the capability."""


def normalize_scope(capability: Capability, hosts: Iterable[str]) -> list[str]:
    """Return the canonical scope: sorted, lower-cased, de-duplicated host names (``[]`` for ``notify``)."""
    names = {host.strip().lower() for host in hosts}
    if capability == "notify":
        if names:
            raise InvalidScopeError("notify takes no scope")
        return []
    if not names:
        raise InvalidScopeError("fetch needs at least one host name")
    if len(names) > MAX_FETCH_HOSTS:
        raise InvalidScopeError(f"fetch allows at most {MAX_FETCH_HOSTS} host names")
    for name in names:
        if not _HOST_NAME.fullmatch(name):
            raise InvalidScopeError(f"not an exact public host name: {name!r}")
    return sorted(names)
