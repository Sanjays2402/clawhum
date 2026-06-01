"""Per-PAT owner-email metadata and workspace ownership inventory.

These tests pin the SOC2 CC6.1 credential-ownership surface end to
end: a workspace owner can declare who a PAT belongs to at mint time
(or set it later via the admin route), the inventory endpoint
separates owned from unowned credentials, malformed addresses are
rejected with a structured 400, and a workspace cannot read or
mutate another workspace's tokens.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:acmekey:10000:admin:acme,umbrella:umbkey:10000:admin:umbrella",
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


def _mint(client, key, *, name, owner_email=None):
    body = {"name": name, "roles": ["writer"]}
    if owner_email is not None:
        body["owner_email"] = owner_email
    r = client.post("/keys", json=body, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


def test_mint_records_owner_email_when_provided(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        view = _mint(c, "acmekey", name="ci-bot", owner_email="On-Call@Example.COM")
        # Stored lowercase, length-validated, surfaced on the view.
        assert view["owner_email"] == "on-call@example.com"
        # Existing fields still flow through (no field-shadowing bug).
        assert view["roles"] == ["writer"]
        assert view["secret"].startswith("pat_")


def test_mint_without_owner_email_keeps_field_blank(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        view = _mint(c, "acmekey", name="legacy-bot")
        assert view["owner_email"] == ""


def test_mint_rejects_malformed_owner_email(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={"name": "bad", "roles": ["writer"], "owner_email": "not-an-email"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 400, r.text
        assert "owner_email" in r.text


def test_set_owner_email_route_updates_existing_pat(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        view = _mint(c, "acmekey", name="ops-bot")
        kid = view["id"]
        r = c.put(
            f"/admin/keys/{kid}/owner-email",
            json={"owner_email": "sre@example.com"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "id": kid, "owner_email": "sre@example.com"}
        # Clearing it back to blank works too.
        r = c.put(
            f"/admin/keys/{kid}/owner-email",
            json={"owner_email": ""},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200
        assert r.json()["owner_email"] == ""


def test_owner_email_route_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        acme_view = _mint(c, "acmekey", name="acme-bot", owner_email="a@acme.test")
        # umbrella cannot read or mutate acme's token; existence must
        # not leak (404, not 403).
        r = c.put(
            f"/admin/keys/{acme_view['id']}/owner-email",
            json={"owner_email": "evil@umbrella.test"},
            headers={"X-API-Key": "umbkey"},
        )
        assert r.status_code == 404, r.text
        # acme still sees its own owner_email unchanged.
        r2 = c.get("/admin/keys/inventory", headers={"X-API-Key": "acmekey"})
        assert r2.status_code == 200
        rows = {row["id"]: row for row in r2.json()["rows"]}
        assert rows[acme_view["id"]]["owner_email"] == "a@acme.test"


def test_inventory_counts_owned_and_unowned(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        _mint(c, "acmekey", name="owned", owner_email="a@acme.test")
        _mint(c, "acmekey", name="orphan")
        # umbrella's PAT must not bleed into acme's inventory.
        _mint(c, "umbkey", name="other-tenant", owner_email="o@umb.test")

        r = c.get("/admin/keys/inventory", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 2
        assert body["with_owner"] == 1
        assert body["without_owner"] == 1
        names = {row["name"] for row in body["rows"]}
        assert names == {"owned", "orphan"}
        assert "other-tenant" not in names


def test_inventory_requires_admin_role(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:readkey:10000:reader:acme",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("CLAWHUM_MFA_REQUIRED_FOR_ADMIN", "false")
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api.app import create_app

    with TestClient(create_app()) as c:
        r = c.get("/admin/keys/inventory", headers={"X-API-Key": "readkey"})
        assert r.status_code == 403, r.text
