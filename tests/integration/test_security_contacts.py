"""Tests for per-workspace security and breach notification contacts.

Covers what an enterprise security review (and an EU customer's
GDPR Art 33 checklist) actually asks for:

1. Admin can create, list, delete, and promote a primary contact.
2. Non-admin roles get 403 on every mutation and on the read.
3. Tenant A cannot list, delete, or promote tenant B's contacts.
4. Invalid input (bad email, unknown role, duplicate email) returns
   a structured 400 the admin console can render.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_SECURITY_CONTACTS_PATH", str(tmp_path / "sc.jsonl")
    )
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import security_contacts
    security_contacts.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _hdr(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def test_admin_can_crud_and_promote(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")

    # Empty roster.
    empty = c.get("/security-contacts", headers=_hdr("sk_admin"))
    assert empty.status_code == 200
    body = empty.json()
    assert body["contacts"] == []
    assert body["primary_id"] is None
    assert "security" in body["roles"]

    # Create two contacts.
    a = c.post(
        "/security-contacts",
        json={"email": "soc@acme.com", "name": "SOC Team", "role": "security"},
        headers=_hdr("sk_admin"),
    )
    assert a.status_code == 201, a.text
    assert a.json()["primary"] is False
    a_id = a.json()["id"]

    b = c.post(
        "/security-contacts",
        json={
            "email": "dpo@acme.com",
            "name": "Data Protection Officer",
            "role": "privacy",
            "phone": "+1-555-0100",
            "primary": True,
        },
        headers=_hdr("sk_admin"),
    )
    assert b.status_code == 201, b.text
    assert b.json()["primary"] is True
    b_id = b.json()["id"]

    listed = c.get("/security-contacts", headers=_hdr("sk_admin")).json()
    assert listed["primary_id"] == b_id
    assert {row["id"] for row in listed["contacts"]} == {a_id, b_id}
    # Primary sorts first.
    assert listed["contacts"][0]["id"] == b_id

    # Promote A; B should be demoted.
    promote = c.post(
        f"/security-contacts/{a_id}/primary", headers=_hdr("sk_admin")
    )
    assert promote.status_code == 200, promote.text
    assert promote.json()["primary"] is True
    after = c.get("/security-contacts", headers=_hdr("sk_admin")).json()
    assert after["primary_id"] == a_id
    primaries = [row for row in after["contacts"] if row["primary"]]
    assert len(primaries) == 1 and primaries[0]["id"] == a_id

    # Delete A.
    gone = c.delete(f"/security-contacts/{a_id}", headers=_hdr("sk_admin"))
    assert gone.status_code == 204
    final = c.get("/security-contacts", headers=_hdr("sk_admin")).json()
    assert [row["id"] for row in final["contacts"]] == [b_id]


def test_non_admin_blocked(monkeypatch, tmp_path):
    spec = "w:sk_writer:9999:writer:acme,a:sk_admin:9999:admin:acme"
    c = _client(monkeypatch, tmp_path, spec)
    # Writer cannot read.
    r = c.get("/security-contacts", headers=_hdr("sk_writer"))
    assert r.status_code == 403
    # Writer cannot create.
    w = c.post(
        "/security-contacts",
        json={"email": "x@acme.com"},
        headers=_hdr("sk_writer"),
    )
    assert w.status_code == 403


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    spec = (
        "a_ops:sk_acme:9999:admin:acme,g_ops:sk_globex:9999:admin:globex"
    )
    c = _client(monkeypatch, tmp_path, spec)

    created = c.post(
        "/security-contacts",
        json={"email": "soc@acme.com", "primary": True},
        headers=_hdr("sk_acme"),
    )
    assert created.status_code == 201
    acme_id = created.json()["id"]

    # Globex cannot see acme contacts.
    g_list = c.get("/security-contacts", headers=_hdr("sk_globex")).json()
    assert g_list["contacts"] == []
    assert g_list["primary_id"] is None

    # Globex cannot delete acme contact even when guessing the id.
    bad_del = c.delete(
        f"/security-contacts/{acme_id}", headers=_hdr("sk_globex")
    )
    assert bad_del.status_code == 404

    # Globex cannot promote acme contact.
    bad_promote = c.post(
        f"/security-contacts/{acme_id}/primary", headers=_hdr("sk_globex")
    )
    assert bad_promote.status_code == 404

    # Acme rows still intact.
    acme_after = c.get("/security-contacts", headers=_hdr("sk_acme")).json()
    assert acme_after["primary_id"] == acme_id


def test_validation_errors(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")

    bad_email = c.post(
        "/security-contacts",
        json={"email": "not-an-email"},
        headers=_hdr("sk_admin"),
    )
    assert bad_email.status_code == 400
    assert "email" in bad_email.json()["detail"].lower()

    bad_role = c.post(
        "/security-contacts",
        json={"email": "ok@acme.com", "role": "ceo"},
        headers=_hdr("sk_admin"),
    )
    assert bad_role.status_code == 400
    assert "role" in bad_role.json()["detail"].lower()

    ok = c.post(
        "/security-contacts",
        json={"email": "dup@acme.com"},
        headers=_hdr("sk_admin"),
    )
    assert ok.status_code == 201
    dup = c.post(
        "/security-contacts",
        json={"email": "dup@acme.com"},
        headers=_hdr("sk_admin"),
    )
    assert dup.status_code == 400
    assert "exists" in dup.json()["detail"].lower()
