"""How a host introduces itself to the agents it delegates to.

[INPUT]
- myrm_agent_harness::__version__ (POS: the version of this package, the default a host announces)

[OUTPUT]
- ClientIdentity: the name, title and version a host announces in the handshake of every agent it starts

[POS]
One identity for both handshakes (the ACP ``initialize`` and the app server's), so a host that embeds the harness
names itself once and every agent it starts sees the same client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from myrm_agent_harness import __version__

# What an agent keeps verbatim when it repeats the name in its own user-agent string (``<name>/<version> (...)``), the
# string the host reads the agent's version back from. Any other character may come back changed, and the version after
# the name would not be found.
_NAME_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True, slots=True)
class ClientIdentity:
    """The client an agent is told it is talking to.

    ``name`` is what the agent's logs and user-agent string show, ``title`` is for people and ``version`` is the
    host's own.
    """

    name: str = "myrm_agent_harness"
    title: str = "Myrm Agent"
    version: str = __version__

    def __post_init__(self) -> None:
        if not _NAME_SHAPE.fullmatch(self.name):
            msg = f"A client name is letters, digits, '.', '_' and '-' and starts with a letter or digit, got {self.name!r}"
            raise ValueError(msg)
        if not self.version.strip():
            msg = "A client version must not be blank"
            raise ValueError(msg)
