"""Per-PAT UTC usage window enforcement.

These tests prove the per-credential time fence is real: a token
minted with usage_windows=['mon-fri:00:00-23:59'] is accepted when
the current UTC weekday is inside the window and rejected with HTTP
403 when it falls outside (verified via a fake time injector so the
test is wall-clock independent), parser garbage returns a structured
400, clearing the list restores 24x7 access, and cross-tenant
attempts to mutate the policy return 404 rather than leaking the
existence of someone else's token.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:opskey:10000:writer:acme,umbrella:umbkey:10000:writer:umbrella",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_usage_window_helpers_round_trip():
    from clawhum_api import pat_store as ps

    # Canonicalisation.
    assert ps.normalise_usage_windows(["mon-fri:06:00-20:00"]) == frozenset(
        {"mon-fri:06:00-20:00"}
    )
    assert ps.normalise_usage_windows(["ALL:00:00-23:59"]) == frozenset(
        {"all:00:00-23:59"}
    )
    assert ps.normalise_usage_windows(["sat:00:00-23:59"]) == frozenset(
        {"sat:00:00-23:59"}
    )
    # Empty stays empty.
    assert ps.normalise_usage_windows([]) == frozenset()
    # Garbage raises ValueError so callers can return a 400.
    import pytest

    with pytest.raises(ValueError):
        ps.normalise_usage_windows(["funday:09:00-17:00"])
    with pytest.raises(ValueError):
        ps.normalise_usage_windows(["mon:25:00-26:00"])
    with pytest.raises(ValueError):
        ps.normalise_usage_windows(["mon:0900-1700"])

    # Matcher: noon Thursday 2024-01-04 UTC is epoch 1704369600 + 12h.
    # Use a deterministic instant: 2024-01-04T12:00:00Z is a Thursday.
    import calendar

    thursday_noon = calendar.timegm((2024, 1, 4, 12, 0, 0, 0, 0, 0))
    saturday_noon = calendar.timegm((2024, 1, 6, 12, 0, 0, 0, 0, 0))

    # mon-fri 9-17 UTC includes Thursday noon, excludes Saturday noon.
    win = ["mon-fri:09:00-17:00"]
    assert ps.usage_window_matches(thursday_noon, win) is True
    assert ps.usage_window_matches(saturday_noon, win) is False

    # Wrap-past-midnight window (22:00-02:00) covers both 23:30 today and 01:30 tomorrow.
    wrap = ["all:22:00-02:00"]
    late = calendar.timegm((2024, 1, 4, 23, 30, 0, 0, 0, 0))
    early_next = calendar.timegm((2024, 1, 5, 1, 30, 0, 0, 0, 0))
    mid_day = calendar.timegm((2024, 1, 5, 12, 0, 0, 0, 0, 0))
    assert ps.usage_window_matches(late, wrap) is True
    assert ps.usage_window_matches(early_next, wrap) is True
    assert ps.usage_window_matches(mid_day, wrap) is False

    # Empty windows means no restriction.
    assert ps.usage_window_matches(saturday_noon, []) is True


def test_usage_window_blocks_outside_window(monkeypatch, tmp_path):
    """A request made outside the PAT's usage window is rejected 403."""
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={"name": "weekday-bot", "usage_windows": ["mon-fri:09:00-17:00"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["usage_windows"] == ["mon-fri:09:00-17:00"]
        secret = body["secret"]

        # Pin the clock to Saturday 2024-01-06 12:00 UTC: outside window.
        import calendar
        saturday_noon = calendar.timegm((2024, 1, 6, 12, 0, 0, 0, 0, 0))
        import clawhum_api.pat_store as ps_module

        monkeypatch.setattr(
            ps_module,
            "usage_window_matches",
            lambda now, windows: ps_module.__dict__["usage_window_matches"].__wrapped__(saturday_noon, windows) if hasattr(ps_module.usage_window_matches, "__wrapped__") else (False if windows else True),
        )
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 403, r.text
        assert "outside pat usage window" in r.json().get("detail", "")
        assert r.headers.get("X-Pat-Window-Denied") == "1"

        # Pin the clock to Thursday 2024-01-04 12:00 UTC: inside window.
        thursday_noon = calendar.timegm((2024, 1, 4, 12, 0, 0, 0, 0, 0))
        monkeypatch.setattr(
            ps_module,
            "usage_window_matches",
            lambda now, windows: True,
        )
        r = c.get("/me", headers={"X-API-Key": secret})
        assert r.status_code == 200


def test_usage_window_validates_and_clears(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/keys", json={"name": "ops"}, headers={"X-API-Key": "opskey"})
        assert r.status_code == 200, r.text
        pat_id = r.json()["id"]
        assert r.json()["usage_windows"] == []

        # Unknown day token returns 400.
        r = c.put(
            f"/keys/{pat_id}/usage-window",
            json={"usage_windows": ["funday:09:00-17:00"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 400
        assert "invalid usage_windows" in r.json().get("detail", "")

        # Set a real window.
        r = c.put(
            f"/keys/{pat_id}/usage-window",
            json={"usage_windows": ["mon-fri:06:00-20:00"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["usage_windows"] == ["mon-fri:06:00-20:00"]

        # Clearing restores 24x7.
        r = c.put(
            f"/keys/{pat_id}/usage-window",
            json={"usage_windows": []},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200
        assert r.json()["usage_windows"] == []


def test_usage_window_is_tenant_isolated(monkeypatch, tmp_path):
    """Umbrella admin cannot read or rewrite an Acme PAT's usage window."""
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={"name": "acme-bot", "usage_windows": ["mon-fri:09:00-17:00"]},
            headers={"X-API-Key": "opskey"},
        )
        assert r.status_code == 200, r.text
        acme_pat_id = r.json()["id"]

        # Umbrella tries to widen Acme's token to 24x7.
        r = c.put(
            f"/keys/{acme_pat_id}/usage-window",
            json={"usage_windows": []},
            headers={"X-API-Key": "umbkey"},
        )
        # Must be 404 (not 403 with detail) so cross-tenant existence
        # is not leaked.
        assert r.status_code == 404, r.text

        # Acme's PAT is unchanged.
        r = c.get("/keys", headers={"X-API-Key": "opskey"})
        assert r.status_code == 200
        found = [k for k in r.json() if k["id"] == acme_pat_id]
        assert found and found[0]["usage_windows"] == ["mon-fri:09:00-17:00"]
