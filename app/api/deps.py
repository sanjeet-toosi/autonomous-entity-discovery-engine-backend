from __future__ import annotations

from fastapi import Header, HTTPException

from app.config import get_settings


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    s = get_settings()
    if s.api_key and x_api_key != s.api_key:
        raise HTTPException(status_code=401, detail="invalid API key")
