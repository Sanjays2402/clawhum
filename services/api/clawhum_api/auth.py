from __future__ import annotations
from fastapi import Header, HTTPException, status
from clawhum_core.settings import get_settings


async def require_api_key(x_api_key: str = Header(default="")) -> str:
    s = get_settings()
    if not s.api_key or s.api_key == "changeme":
        return "dev"
    if x_api_key != s.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    return x_api_key
