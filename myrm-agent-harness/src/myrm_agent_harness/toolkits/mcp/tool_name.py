"""The shape and the length budget of the name the model sees for an MCP tool.

[INPUT]
- hashlib (stdlib): sha256 digest of the name a clamp shortens

[OUTPUT]
- TOOL_NAME_MARKER, TOOL_NAME_DELIMITER: the literal text around the server identity in ``mcp__{server}__{tool}``
- MAX_TOOL_NAME_LENGTH: the function-name budget, 64 characters
- TOOL_NAME_DIGEST_LENGTH: the hex characters of the digest a clamped name ends with
- MAX_IDENTITY_LENGTH: the longest server identity, the longest one whose prefix still leaves room for that digest
- clamp_tool_name: ``prefix + segment`` shortened to the budget behind a digest of the whole name

[POS]
Leaf of the MCP toolkit (it imports nothing of the package), so the identity rule in ``server_identity`` and the name
composition in ``config.mcp_tool_name`` read one budget. OpenAI documents 64 characters as the longest function name
(``tools[].function.name`` of the Chat Completions API) and Anthropic 128 (``^[a-zA-Z0-9_-]{1,128}$``); the budget is the
stricter of the two, so one name serves either. The server identity is bounded for the same reason: the prefix of any
server keeps room for the digest, which is what lets a clamp shorten only the tool segment and leave the prefix, and
everything keyed by it, alone.
"""

from __future__ import annotations

import hashlib

TOOL_NAME_MARKER = "mcp__"
TOOL_NAME_DELIMITER = "__"
MAX_TOOL_NAME_LENGTH = 64
TOOL_NAME_DIGEST_LENGTH = 8
MAX_IDENTITY_LENGTH = MAX_TOOL_NAME_LENGTH - len(TOOL_NAME_MARKER) - len(TOOL_NAME_DELIMITER) - TOOL_NAME_DIGEST_LENGTH


def clamp_tool_name(prefix: str, segment: str) -> str:
    """Return ``prefix + segment``, shortened to ``MAX_TOOL_NAME_LENGTH`` when it is longer.

    A name that fits is returned byte for byte. A longer one keeps the whole prefix and the readable head of the
    segment, then ``_`` and a digest of the whole original name, so two tools whose names differ only after the cut
    stay distinct. Both arguments are ASCII: a server identity and a folded tool name. The cut never leaves a trailing
    ``_`` before the separator, which keeps ``__`` the one delimiter of the name.
    """
    name = prefix + segment
    if len(name) <= MAX_TOOL_NAME_LENGTH:
        return name
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:TOOL_NAME_DIGEST_LENGTH]
    room = MAX_TOOL_NAME_LENGTH - len(prefix) - TOOL_NAME_DIGEST_LENGTH - 1
    readable = segment[:room].rstrip("_") if room > 0 else ""
    return f"{prefix}{readable}_{digest}" if readable else f"{prefix}{digest}"
