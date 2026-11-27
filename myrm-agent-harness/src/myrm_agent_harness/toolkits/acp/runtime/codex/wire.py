"""Typed reads of the decoded JSON that Codex's app server sends.

[INPUT]
no — leaf module, standard library only

[OUTPUT]
- Json: a decoded JSON object
- as_obj, as_str, as_list, as_int, as_bool: the value as that type, or an empty one when the server sent something else

[POS]
Everything the server sends is untrusted shape: a field may be missing, null or another type after an upgrade. The
readers of ``turn_events``, ``approvals`` and ``failure`` go through these instead of casting, so a surprise degrades
to "nothing there" and never to an exception inside the turn loop.
"""

from __future__ import annotations

from collections.abc import Mapping

type Json = Mapping[str, object]

_EMPTY: Json = {}


def as_obj(value: object) -> Json:
    """``value`` when it is a JSON object, else an empty one."""
    return value if isinstance(value, dict) else _EMPTY


def as_str(value: object) -> str:
    """``value`` when it is a string, else an empty one."""
    return value if isinstance(value, str) else ""


def as_list(value: object) -> list[object]:
    """``value`` when it is a JSON array, else an empty one."""
    return value if isinstance(value, list) else []


def as_int(value: object) -> int:
    """``value`` when it is a number that counts something (a bool does not), else 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def as_bool(value: object) -> bool:
    """``value`` when it is a boolean, else False."""
    return value is True
