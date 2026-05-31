from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str = "main:changeme:9999:writer"):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_SHARES_PATH", str(tmp_path / "shares.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _sample_body():
    return {
        "query_id": "q-abc",
        "elapsed_ms": 42,
        "count": 2,
        "results": [
            {"track_id": "t1", "title": "Test Song", "artist": "Tester", "score": 0.91, "segment_index": 3},
            {"track_id": "t2", "title": "Other", "artist": "Other", "score": 0.55, "segment_index": 0},
        ],
        "filename": "hum.wav",
        "duration_sec": 4.2,
    }


def test_share_create_and_read_public(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # create requires auth
        r0 = c.post("/share", json=_sample_body())
        assert r0.status_code == 401

        r = c.post("/share", json=_sample_body(), headers={"X-API-Key": "changeme"})
        assert r.status_code == 200, r.text
        body = r.json()
        share_id = body["id"]
        assert share_id and len(share_id) == 12
        assert body["url_path"] == f"/r/{share_id}"

        # read is public (no api key header)
        r2 = c.get(f"/share/{share_id}")
        assert r2.status_code == 200, r2.text
        got = r2.json()
        assert got["id"] == share_id
        assert got["query_id"] == "q-abc"
        assert got["count"] == 2
        assert got["results"][0]["title"] == "Test Song"
        assert got["filename"] == "hum.wav"


def test_share_validates_empty_results(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        bad = _sample_body()
        bad["results"] = []
        r = c.post("/share", json=bad, headers={"X-API-Key": "changeme"})
        assert r.status_code == 400


def test_share_missing_id_returns_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/share/notarealid12")
        assert r.status_code == 404
        r2 = c.get("/share/bad..id")
        assert r2.status_code == 404


def test_share_list_and_revoke(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # list requires auth
        assert c.get("/share").status_code == 401

        # empty list to start
        r0 = c.get("/share", headers={"X-API-Key": "changeme"})
        assert r0.status_code == 200, r0.text
        assert r0.json() == {"shares": [], "total": 0}

        # create two shares
        r1 = c.post("/share", json=_sample_body(), headers={"X-API-Key": "changeme"})
        r2 = c.post("/share", json=_sample_body(), headers={"X-API-Key": "changeme"})
        id1 = r1.json()["id"]
        id2 = r2.json()["id"]
        assert id1 != id2

        listed = c.get("/share", headers={"X-API-Key": "changeme"}).json()
        assert listed["total"] == 2
        ids = {s["id"] for s in listed["shares"]}
        assert ids == {id1, id2}
        first = listed["shares"][0]
        assert first["url_path"] == f"/r/{first['id']}"
        assert first["top_title"] == "Test Song"
        assert first["top_artist"] == "Tester"
        assert first["count"] == 2

        # revoke needs auth
        assert c.delete(f"/share/{id1}").status_code == 401

        # revoke succeeds
        dr = c.delete(f"/share/{id1}", headers={"X-API-Key": "changeme"})
        assert dr.status_code == 200, dr.text
        assert dr.json() == {"ok": True, "id": id1}

        # public read now 404
        assert c.get(f"/share/{id1}").status_code == 404
        # list no longer contains it
        after = c.get("/share", headers={"X-API-Key": "changeme"}).json()
        assert after["total"] == 1
        assert after["shares"][0]["id"] == id2

        # second revoke returns 404
        assert c.delete(f"/share/{id1}", headers={"X-API-Key": "changeme"}).status_code == 404


def test_share_revoke_is_tenant_scoped(monkeypatch, tmp_path):
    api_keys = "alice:secret-a:9999:writer:tenant-a,bob:secret-b:9999:writer:tenant-b"
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        r = c.post("/share", json=_sample_body(), headers={"X-API-Key": "secret-a"})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]

        # bob cannot see alice's share in his list
        bobs = c.get("/share", headers={"X-API-Key": "secret-b"}).json()
        assert bobs == {"shares": [], "total": 0}

        # bob cannot revoke alice's share
        deny = c.delete(f"/share/{sid}", headers={"X-API-Key": "secret-b"})
        assert deny.status_code == 404

        # but public read still works (it's a public link)
        assert c.get(f"/share/{sid}").status_code == 200

        # alice can revoke her own
        ok = c.delete(f"/share/{sid}", headers={"X-API-Key": "secret-a"})
        assert ok.status_code == 200
        assert c.get(f"/share/{sid}").status_code == 404


def test_share_patch_note_round_trip(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        body = _sample_body()
        body["note"] = "first cut"
        r = c.post("/share", json=body, headers={"X-API-Key": "changeme"})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]

        # patch requires auth
        assert c.patch(f"/share/{sid}", json={"note": "nope"}).status_code == 401

        # rename the note
        p = c.patch(
            f"/share/{sid}",
            json={"note": "  cleaned up takes  "},
            headers={"X-API-Key": "changeme"},
        )
        assert p.status_code == 200, p.text
        assert p.json()["note"] == "cleaned up takes"
        # results are preserved across the partial update
        assert p.json()["top_title"] == "Test Song"

        # public read reflects the update
        pub = c.get(f"/share/{sid}").json()
        assert pub["note"] == "cleaned up takes"
        assert pub["count"] == 2
        assert pub["results"][0]["title"] == "Test Song"

        # list reflects the update too
        listed = c.get("/share", headers={"X-API-Key": "changeme"}).json()
        assert listed["total"] == 1
        assert listed["shares"][0]["note"] == "cleaned up takes"

        # clearing the note via empty string drops it back to None
        p2 = c.patch(
            f"/share/{sid}",
            json={"note": "   "},
            headers={"X-API-Key": "changeme"},
        )
        assert p2.status_code == 200
        assert p2.json()["note"] is None
        assert c.get(f"/share/{sid}").json()["note"] is None


def test_share_patch_404_for_missing_and_other_tenant(monkeypatch, tmp_path):
    api_keys = "alice:secret-a:9999:writer:tenant-a,bob:secret-b:9999:writer:tenant-b"
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        # patch a nonexistent share
        miss = c.patch(
            "/share/zzzzzzzzzzzz",
            json={"note": "x"},
            headers={"X-API-Key": "secret-a"},
        )
        assert miss.status_code == 404

        # alice creates, bob cannot patch
        r = c.post("/share", json=_sample_body(), headers={"X-API-Key": "secret-a"})
        sid = r.json()["id"]
        deny = c.patch(
            f"/share/{sid}",
            json={"note": "hijack"},
            headers={"X-API-Key": "secret-b"},
        )
        assert deny.status_code == 404

        # revoked share cannot be patched
        c.delete(f"/share/{sid}", headers={"X-API-Key": "secret-a"})
        gone = c.patch(
            f"/share/{sid}",
            json={"note": "ghost"},
            headers={"X-API-Key": "secret-a"},
        )
        assert gone.status_code == 404


def _client_with_share_ttl(monkeypatch, tmp_path, *, default_days=0, max_days=365):
    monkeypatch.setenv("CLAWHUM_SHARE_DEFAULT_TTL_DAYS", str(default_days))
    monkeypatch.setenv("CLAWHUM_SHARE_MAX_TTL_DAYS", str(max_days))
    return _client(monkeypatch, tmp_path)


def test_share_create_honours_requested_expiry(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        body = _sample_body()
        body["expires_in_days"] = 7
        r = c.post("/share", json=body, headers={"X-API-Key": "changeme"})
        assert r.status_code == 200, r.text
        created = r.json()
        sid = created["id"]
        assert created["expires_at"] > 0
        pub = c.get(f"/share/{sid}")
        assert pub.status_code == 200
        assert pub.json()["expires_at"] == created["expires_at"]
        listed = c.get("/share", headers={"X-API-Key": "changeme"}).json()
        assert listed["total"] == 1
        row = listed["shares"][0]
        assert row["expires_at"] == created["expires_at"]
        assert row["expired"] is False


def test_share_create_default_no_expiry(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/share", json=_sample_body(), headers={"X-API-Key": "changeme"})
        assert r.status_code == 200
        assert r.json()["expires_at"] == 0.0
        listed = c.get("/share", headers={"X-API-Key": "changeme"}).json()
        assert listed["shares"][0]["expired"] is False
        assert listed["shares"][0]["expires_at"] == 0.0


def test_share_workspace_default_ttl_applied(monkeypatch, tmp_path):
    with _client_with_share_ttl(monkeypatch, tmp_path, default_days=14) as c:
        r = c.post("/share", json=_sample_body(), headers={"X-API-Key": "changeme"})
        assert r.status_code == 200
        exp = r.json()["expires_at"]
        assert exp > 0
        import time as _t
        assert 12 * 86400 < (exp - _t.time()) < 16 * 86400


def test_share_max_ttl_clamps_request(monkeypatch, tmp_path):
    with _client_with_share_ttl(monkeypatch, tmp_path, default_days=0, max_days=5) as c:
        body = _sample_body()
        body["expires_in_days"] = 30
        r = c.post("/share", json=body, headers={"X-API-Key": "changeme"})
        assert r.status_code == 200
        exp = r.json()["expires_at"]
        import time as _t
        assert 4 * 86400 < (exp - _t.time()) < 6 * 86400


def test_share_expired_public_get_returns_410(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        body = _sample_body()
        body["expires_in_days"] = 1
        r = c.post("/share", json=body, headers={"X-API-Key": "changeme"})
        sid = r.json()["id"]

        import json as _json
        from pathlib import Path as _P
        p = _P(tmp_path / "shares.jsonl")
        lines = p.read_text().splitlines()
        rec = _json.loads(lines[-1])
        rec["expires_at"] = 1.0
        lines[-1] = _json.dumps(rec)
        p.write_text("\n".join(lines) + "\n")

        gone = c.get(f"/share/{sid}")
        assert gone.status_code == 410
        listed = c.get("/share", headers={"X-API-Key": "changeme"}).json()
        assert listed["total"] == 1
        assert listed["shares"][0]["expired"] is True

        ext = c.patch(
            f"/share/{sid}",
            json={"expires_in_days": 30},
            headers={"X-API-Key": "changeme"},
        )
        assert ext.status_code == 200
        assert ext.json()["expired"] is False
        again = c.get(f"/share/{sid}")
        assert again.status_code == 200


def test_share_patch_can_clear_expiry(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        body = _sample_body()
        body["expires_in_days"] = 3
        sid = c.post("/share", json=body, headers={"X-API-Key": "changeme"}).json()["id"]
        cleared = c.patch(
            f"/share/{sid}",
            json={"expires_in_days": 0},
            headers={"X-API-Key": "changeme"},
        )
        assert cleared.status_code == 200
        assert cleared.json()["expires_at"] == 0.0
        assert c.get(f"/share/{sid}").json()["expires_at"] == 0.0


def test_share_expiry_isolated_across_tenants(monkeypatch, tmp_path):
    api_keys = "alice:secret-a:9999:writer:tenant-a,bob:secret-b:9999:writer:tenant-b"
    with _client(monkeypatch, tmp_path, api_keys=api_keys) as c:
        body = _sample_body()
        body["expires_in_days"] = 7
        sid = c.post(
            "/share", json=body, headers={"X-API-Key": "secret-a"}
        ).json()["id"]
        deny = c.patch(
            f"/share/{sid}",
            json={"expires_in_days": 1000},
            headers={"X-API-Key": "secret-b"},
        )
        assert deny.status_code == 404
        alice_list = c.get("/share", headers={"X-API-Key": "secret-a"}).json()
        assert alice_list["total"] == 1
        assert alice_list["shares"][0]["expires_at"] > 0
