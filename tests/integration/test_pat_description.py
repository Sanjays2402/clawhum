"""Per-PAT description / purpose-note metadata and inventory surface.

These tests pin the SOC2 / ISO 27001 access-review surface end to
end: a workspace owner can declare what a PAT is for at mint time
(or set it later via the admin route), the inventory endpoint
counts documented vs undocumented credentials, overlong input is
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


def _mint(client, key, *, name, description=None):
    body = {"name": name, "roles": ["writer"]}
    if description is not None:
        body["description"] = description
    r = client.post("/keys", json=body, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


def test_mint_records_description_when_provided(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        view = _mint(
            c,
            "acmekey",
            name="ci-bot",
            description="  CI deploy bot,\nowned by platform-eng  ",
        )
        # Whitespace collapsed, surfaced on the view.
        assert view["description"] == "CI deploy bot, owned by platform-eng"
        assert view["roles"] == ["writer"]
        assert view["secret"].startswith("pat_")


def test_mint_without_description_keeps_field_blank(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        view = _mint(c, "acmekey", name="legacy-bot")
        assert view["description"] == ""


def test_mint_rejects_overlong_description(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/keys",
            json={
                "name": "bad",
                "roles": ["writer"],
                "description": "x" * 500,
            },
            headers={"X-API-Key": "acmekey"},
        )
        # Pydantic max_length kicks in first with a 422; either way
        # the request is refused before the token is minted.
        assert r.status_code in (400, 422), r.text


def test_set_description_route_updates_existing_pat(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        view = _mint(c, "acmekey", name="ops-bot")
        kid = view["id"]
        r = c.put(
            f"/admin/keys/{kid}/description",
            json={"description": "nightly backup job"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {
            "ok": True,
            "id": kid,
            "description": "nightly backup job",
        }
        # Clearing it back to blank works too.
        r = c.put(
            f"/admin/keys/{kid}/description",
            json={"description": ""},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 200
        assert r.json()["description"] == ""


def test_description_route_is_tenant_scoped(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        acme_view = _mint(
            c, "acmekey", name="acme-bot", description="acme integration"
        )
        # umbrella cannot read or mutate acme's token; existence must
        # not leak (404, not 403).
        r = c.put(
            f"/admin/keys/{acme_view['id']}/description",
            json={"description": "rewritten by attacker"},
            headers={"X-API-Key": "umbkey"},
        )
        assert r.status_code == 404, r.text
        # acme still sees its own description unchanged.
        r2 = c.get("/admin/keys/inventory", headers={"X-API-Key": "acmekey"})
        assert r2.status_code == 200
        rows = {row["id"]: row for row in r2.json()["rows"]}
        assert rows[acme_view["id"]]["description"] == "acme integration"


def test_inventory_counts_documented_and_undocumented(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        _mint(c, "acmekey", name="documented", description="ingest pipeline")
        _mint(c, "acmekey", name="undocumented")
        # umbrella's PAT must not bleed into acme's inventory.
        _mint(c, "umbkey", name="other-tenant", description="theirs")

        r = c.get("/admin/keys/inventory", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 2
        assert body["with_description"] == 1
        assert body["without_description"] == 1
        names_by_desc = {row["name"]: row["description"] for row in body["rows"]}
        assert names_by_desc == {
            "documented": "ingest pipeline",
            "undocumented": "",
        }


def test_set_description_requires_admin_role(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme:writekey:10000:writer:acme",
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
        # Writer mints their own PAT, then is denied the admin route.
        r0 = c.post(
            "/keys",
            json={"name": "self", "roles": ["writer"]},
            headers={"X-API-Key": "writekey"},
        )
        assert r0.status_code == 200, r0.text
        kid = r0.json()["id"]
        r = c.put(
            f"/admin/keys/{kid}/description",
            json={"description": "trying to escalate"},
            headers={"X-API-Key": "writekey"},
        )
        assert r.status_code == 403, r.text
