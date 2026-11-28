"""MCP server identity: the ``{server}`` segment of ``mcp__{server}__{tool}``.

[INPUT]
- utils.unicode_normalize::normalize (POS: Unicode normalization in time linear in the text, the one entry for text that can come from a user or a plugin)
- tool_name::MAX_IDENTITY_LENGTH (POS: the longest identity whose prefix still leaves room for a clamped tool name's digest)

[OUTPUT]
- fold_mcp_name: lossy ASCII fold of a name; empty when no ASCII letter, digit or underscore survives
- mcp_server_component: the identity segment of a server display name

[POS]
Single source of the server identity rule. The segment is part of every key that tells two servers' tools apart
(tool registry, approval lookup, eviction, "always allow" rules, per-tool permission rules), so two servers must not
share one. Folding alone is many-to-one (every CJK name folds to ``unnamed``), so a name that folding would blur gets a
stable hash suffix, while an ASCII name that survives folding keeps its folded form: every name that worked before
keeps its segment, up to ``MAX_IDENTITY_LENGTH`` characters. No identity is longer: a name past that bound keeps a
readable head and ends with the hash suffix, so the prefix of any server leaves a tool name room within the
64-character budget of ``tool_name``. The frontend mirrors the rule in ``mcpServerIdentity.ts``; both sides are pinned by the vectors in
``shared/config/mcp_server_identity.json``. A name that stacks more than 30 combining marks (never natural text) is
normalised in Stream-Safe form here and plainly there, so its hash segment differs between the two sides; this side
decides.
"""

from __future__ import annotations

import re

from myrm_agent_harness.utils.unicode_normalize import normalize

from .tool_name import MAX_IDENTITY_LENGTH

_FNV_OFFSET_BASIS = 0x811C9DC5
_FNV_PRIME = 0x01000193
_SUFFIX_HEX_DIGITS = 6
# What a stem may keep so that ``{stem}_{hash}`` still fits the bound.
_STEM_LENGTH = MAX_IDENTITY_LENGTH - _SUFFIX_HEX_DIGITS - 1


def fold_mcp_name(value: str) -> str:
    """Replace every character outside ``[A-Za-z0-9_]`` with ``_``, collapse ``_`` runs and strip them.

    The result never contains ``__``, which keeps the ``mcp__{server}__{tool}`` delimiter unambiguous.
    """
    folded = re.sub(r"[^A-Za-z0-9_]", "_", value)
    return re.sub(r"_{2,}", "_", folded).strip("_")


def _utf8(text: str) -> bytes:
    """UTF-8 bytes of *text*; a lone surrogate (half of a cut emoji) becomes U+FFFD, as in JavaScript's ``TextEncoder``.

    A plain ``text.encode("utf-8")`` raises for such a name, and no name may make the identity rule fail.
    """
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace").encode("utf-8")


def _stable_suffix(text: str) -> str:
    """First six hex digits of the 32-bit FNV-1a hash of *text* as UTF-8, so Python and TypeScript agree bit for bit."""
    digest = _FNV_OFFSET_BASIS
    for byte in _utf8(text):
        digest = ((digest ^ byte) * _FNV_PRIME) & 0xFFFFFFFF
    return f"{digest:08x}"[:_SUFFIX_HEX_DIGITS]


def _with_hash(stem: str, text: str) -> str:
    """``{stem}_{hash of text}`` with the stem cut to what keeps the identity within ``MAX_IDENTITY_LENGTH``.

    The cut never leaves a trailing ``_``, and a stem with nothing left reads ``unnamed``, so the identity stays a
    readable word followed by one ``_`` and the hash.
    """
    return f"{stem[:_STEM_LENGTH].rstrip('_') or 'unnamed'}_{_stable_suffix(text)}"


def mcp_server_component(server_name: str) -> str:
    """Return the ``{server}`` segment that identifies *server_name* in ``mcp__{server}__{tool}``.

    The name is NFC-normalised first, so a composed and a decomposed spelling of one word are one server. A plugin
    can declare any name, so the normalisation runs in linear time however many combining marks the name stacks.
    An ASCII name that folds to something keeps the folded form (``my-api`` is ``my_api``) while that fits
    ``MAX_IDENTITY_LENGTH``; a longer one keeps a readable head and the hash of its folded form, so every spelling of
    it (``a-b``, ``a_b``, ``a b``) is still one server. Any other name is ``{folded or "unnamed"}_{hash}``, with the
    head cut to the bound: ``天气`` and ``我的服务`` no longer both become ``unnamed``.

    The hash only tells distinct names apart; uniqueness is enforced where servers are registered, so a hash
    collision or a crafted name is rejected there instead of being mixed up with another server.
    """
    name = normalize("NFC", str(server_name or ""))
    folded = fold_mcp_name(name)
    if name.isascii() and folded:
        return folded if len(folded) <= MAX_IDENTITY_LENGTH else _with_hash(folded, folded)
    return _with_hash(folded, name)
