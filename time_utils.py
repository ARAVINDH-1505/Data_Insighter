"""UTC time helpers that preserve the legacy ``...Z`` ISO string format.

``datetime.utcnow()`` is deprecated from Python 3.12 onward. This module
provides direct, timezone-aware replacements that still emit the same
trailing-``Z`` ISO strings the rest of the codebase persists on disk.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return a naive UTC ``datetime`` matching ``datetime.utcnow()``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_iso() -> str:
    """Return an ISO 8601 UTC timestamp with a trailing ``Z`` suffix."""
    return utcnow().isoformat() + 'Z'


def utcnow_stamp(fmt: str = '%Y%m%d%H%M%S') -> str:
    """Return a UTC timestamp formatted with ``fmt`` (default compact id form)."""
    return utcnow().strftime(fmt)
