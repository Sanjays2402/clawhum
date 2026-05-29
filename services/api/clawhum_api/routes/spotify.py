from __future__ import annotations
import secrets
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from clawhum_library.spotify import SpotifyLibrary, SpotifyAuth

router = APIRouter(tags=["spotify"], prefix="/auth/spotify")
_STATE: dict[str, bool] = {}
_AUTH = SpotifyAuth()


@router.get("/login")
async def login():
    sp = SpotifyLibrary(_AUTH)
    state = secrets.token_urlsafe(16)
    _STATE[state] = True
    return RedirectResponse(sp.authorize_url(state))


@router.get("/callback")
async def callback(code: str = "", state: str = ""):
    if state not in _STATE:
        raise HTTPException(400, "bad state")
    _STATE.pop(state, None)
    sp = SpotifyLibrary(_AUTH)
    try:
        auth = sp.exchange_code(code)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "expires_in": int(auth.expires_at)}
