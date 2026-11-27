"""UTC normalization for datetimes read back from SQLite.

[INPUT]
- (none)

[OUTPUT]
- as_utc: a datetime as an aware UTC value

[POS]
SQLite returns every datetime without a zone, while the app writes UTC. Comparing a stored value with ``datetime.now(UTC)``
or with another zoned value needs it normalized first.
"""

from __future__ import annotations

from datetime import datetime, timezone


def as_utc(moment: datetime) -> datetime:
    """``moment`` as an aware UTC value; a naive one is already UTC, because the app only stores UTC."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
