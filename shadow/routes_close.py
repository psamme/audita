"""Month-end close status. Read-only, no model call. See shadow/close.py."""
from fastapi import APIRouter, HTTPException

from shadow import close, db

router = APIRouter()


@router.get("/api/close/{client}")
def close_status(client: str, period: str = "2026-04", track: str = "main"):
    if client not in ("A", "B") or not db.db_path(client).exists() or not track.isidentifier():
        raise HTTPException(404, "no such client")
    try:
        return close.status(client, period, track)
    except ValueError as e:
        raise HTTPException(404, str(e))
