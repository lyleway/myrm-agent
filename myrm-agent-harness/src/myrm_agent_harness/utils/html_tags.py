"""Tag stripping whose cost grows linearly with the text, however broken the markup is.

[INPUT]
- (none)

[OUTPUT]
- strip_tags: The text with every ``<...>`` tag replaced

[POS]
The one tag stripper for text that can come from a remote page, feed or file. The plain pattern ``<[^>]+>`` tries each
"<" against the whole rest of the text, so a run of "<" with no ">" after it costs quadratic time (200,000 of them: about
20 s with the interpreter lock held, from 200 KB of input); ``strip_tags`` returns the same result in time proportional
to the length.
"""

from __future__ import annotations

import re

_TAG = re.compile(r"<[^>]+>")


def strip_tags(text: str, *, replacement: str = "") -> str:
    """Replace every ``<...>`` sequence that holds at least one character with ``replacement``; ``<>`` stays.

    The result is exactly ``re.sub(r"<[^>]+>", replacement, text)``. A match ends at a ">", so a "<" past the last ">"
    can never close: that tail is passed through and only the head is substituted. Every "<" of the head has a ">"
    after it, so each attempt ends at the first one and the pass visits each character a bounded number of times.
    ``replacement`` goes in literally, never read as a pattern template.
    """
    head_end = text.rfind(">") + 1
    return _TAG.sub(replacement.replace("\\", "\\\\"), text[:head_end]) + text[head_end:]
