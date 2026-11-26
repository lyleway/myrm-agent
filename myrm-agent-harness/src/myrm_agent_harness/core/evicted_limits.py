"""How much text one evicted file keeps, shared by the store that writes it and the toolkits that size their output to it.

[INPUT]
- (none — pure constants)

[OUTPUT]
- MAX_STORED_CHARS: The most characters one evicted file keeps; the text beyond it is dropped and the file ends with a cap marker

[POS]
Framework-agnostic evicted-file capacity. Lives in ``core/`` because the store (``agent/``) enforces it and a
toolkit that hands the store a whole document (``toolkits/web_fetch``) needs the same number to ask for no more than
the store can keep, while ``toolkits/`` must not import ``agent/``.
"""

from __future__ import annotations

MAX_STORED_CHARS = 2_000_000

__all__ = ["MAX_STORED_CHARS"]
