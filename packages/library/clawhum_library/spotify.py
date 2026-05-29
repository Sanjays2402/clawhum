from __future__ import annotations
import base64
import time
from dataclasses import dataclass, field
import httpx
from clawhum_core.settings import get_settings
from clawhum_core.types import Track
from clawhum_core.errors import LibraryError, AuthError


@dataclass
class SpotifyAuth:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    scope: str = "playlist-read-private playlist-read-collaborative user-library-read"

    def expired(self) -> bool:
        return time.time() >= self.expires_at - 30


class SpotifyLibrary:
    """Minimal Spotify client. Auth code flow. Preview URLs only."""

    AUTH = "https://accounts.spotify.com"
    API = "https://api.spotify.com/v1"

    def __init__(self, auth: SpotifyAuth | None = None):
        s = get_settings()
        self.client_id = s.spotify_client_id
        self.client_secret = s.spotify_client_secret
        self.redirect_uri = s.spotify_redirect_uri
        self.auth = auth or SpotifyAuth()

    def authorize_url(self, state: str) -> str:
        if not self.client_id:
            raise AuthError("SPOTIFY_CLIENT_ID not configured")
        from urllib.parse import urlencode
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": self.auth.scope,
            "state": state,
        }
        return f"{self.AUTH}/authorize?{urlencode(params)}"

    def _basic(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def exchange_code(self, code: str) -> SpotifyAuth:
        if not self.client_id or not self.client_secret:
            raise AuthError("Spotify credentials missing")
        r = httpx.post(f"{self.AUTH}/api/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        }, headers={"Authorization": self._basic()}, timeout=20.0)
        if r.status_code >= 400:
            raise AuthError(f"token exchange failed: {r.status_code} {r.text}")
        d = r.json()
        self.auth = SpotifyAuth(
            access_token=d["access_token"],
            refresh_token=d.get("refresh_token", ""),
            expires_at=time.time() + d.get("expires_in", 3600),
        )
        return self.auth

    def refresh(self) -> None:
        if not self.auth.refresh_token:
            raise AuthError("no refresh token")
        r = httpx.post(f"{self.AUTH}/api/token", data={
            "grant_type": "refresh_token",
            "refresh_token": self.auth.refresh_token,
        }, headers={"Authorization": self._basic()}, timeout=20.0)
        if r.status_code >= 400:
            raise AuthError(f"refresh failed: {r.status_code}")
        d = r.json()
        self.auth.access_token = d["access_token"]
        self.auth.expires_at = time.time() + d.get("expires_in", 3600)

    def _get(self, path: str, **params) -> dict:
        if self.auth.expired() and self.auth.refresh_token:
            self.refresh()
        r = httpx.get(f"{self.API}{path}", headers={"Authorization": f"Bearer {self.auth.access_token}"},
                      params=params, timeout=20.0)
        if r.status_code >= 400:
            raise LibraryError(f"spotify {path}: {r.status_code}")
        return r.json()

    def playlist_tracks(self, playlist_id: str, limit: int = 100) -> list[Track]:
        items: list[Track] = []
        offset = 0
        while True:
            d = self._get(f"/playlists/{playlist_id}/tracks", limit=min(100, limit), offset=offset)
            for it in d.get("items", []):
                t = it.get("track") or {}
                if not t.get("id"):
                    continue
                items.append(Track(
                    id=f"spotify:{t['id']}",
                    title=t.get("name", ""),
                    artist=", ".join(a.get("name", "") for a in t.get("artists", [])),
                    album=(t.get("album") or {}).get("name", ""),
                    duration_s=(t.get("duration_ms", 0) or 0) / 1000.0,
                    preview_url=t.get("preview_url"),
                    artwork_url=((t.get("album") or {}).get("images") or [{}])[0].get("url"),
                    source="spotify",
                ))
                if len(items) >= limit:
                    return items
            if not d.get("next"):
                break
            offset += 100
        return items

    def download_preview(self, url: str) -> bytes:
        r = httpx.get(url, timeout=20.0, follow_redirects=True)
        if r.status_code >= 400:
            raise LibraryError(f"preview download failed: {r.status_code}")
        return r.content
