from __future__ import annotations

from datetime import datetime, timezone

def utc_now_iso() -> str:
    """
    UTC timestamp string for DB records and export contracts.
    """
    return datetime.now(timezone.utc).isoformat()