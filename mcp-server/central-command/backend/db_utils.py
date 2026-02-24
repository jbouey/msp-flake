"""Shared database utilities — no app-level imports to avoid circular deps."""

import uuid as _uuid
from datetime import datetime, date
from decimal import Decimal
from fastapi import HTTPException


def _uid(s) -> _uuid.UUID:
    """Convert string path param to UUID for asyncpg. Passes UUID objects through."""
    if isinstance(s, _uuid.UUID):
        return s
    try:
        return _uuid.UUID(str(s))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid ID format")


def _row_dict(row):
    """Convert asyncpg Record to dict with JSON-safe values."""
    if row is None:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, _uuid.UUID):
            d[k] = str(v)
        elif isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, date):
            d[k] = v.isoformat()
        elif isinstance(v, Decimal):
            d[k] = float(v)
    return d


def _rows_list(rows):
    return [_row_dict(r) for r in rows]
