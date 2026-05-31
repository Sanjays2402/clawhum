"""Tests for the per-workspace invite email-domain allowlist.

What an enterprise security review actually checks here:

1. With no rules, invite behaviour is unchanged (opt-in feature).
2. With rules, an out-of-policy email is rejected at /members/invite
   with HTTP 422 and a machine-parseable error code.
3. With rules, an in-policy email succeeds and acceptance still works.
4. Subdomain matching only relaxes the rule downward.
5. Tenant A's rules are invisible to tenant B and do not affect
   tenant B's invites (no cross-tenant leakage at the query layer).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_MEMBERS_PATH", str(tmp_path / "members.jsonl"))
    monkeypatch.setenv("CLAWHUM_PAT_PATH", str(tmp_path / "pats.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_INVITE_DOMAINS_PATH", str(tmp_path / "invite_domains.jsonl")
    )
    monkeypatch.setenv(
        "CLAWHUM_API_KEYS",
        "acme_admin:acmekey:10000:admin:acme,"
        "globex_admin:globexkey:10000:admin:globex",
    )
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings

    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache

    reset_registry_cache()
    from clawhum_api import invite_domains

    invite_domains.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_no_rules_means_no_restriction(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/invite-domains", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        body = r.json()
        assert body["enforcing"] is False
        assert body["domains"] == []

        r = c.post(
            "/members/invite",
            json={"email": "alice@anywhere.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text


def test_out_of_policy_email_blocked(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/invite-domains",
            json={"domain": "acme.test", "label": "corp"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text

        r = c.post(
            "/members/invite",
            json={"email": "evil@gmail.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "invite_domain_not_allowed"
        assert detail["email"] == "evil@gmail.test"

        # And in-policy still works, and the issued invite accepts.
        r = c.post(
            "/members/invite",
            json={"email": "alice@acme.test", "role": "writer"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text
        token = r.json()["invite_token"]
        r = c.post("/members/accept", json={"token": token})
        assert r.status_code == 200
        assert r.json()["status"] == "active"


def test_subdomain_flag_only_relaxes_downward(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post(
            "/invite-domains",
            json={"domain": "acme.test", "include_subdomains": True},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201

        # Subdomain is OK.
        r = c.post(
            "/members/invite",
            json={"email": "bob@eu.acme.test", "role": "reader"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201, r.text

        # Lookalike that merely ends in the same string is NOT OK.
        r = c.post(
            "/members/invite",
            json={"email": "mallory@evil-acme.test", "role": "reader"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 422


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        # Acme adds a strict allowlist.
        r = c.post(
            "/invite-domains",
            json={"domain": "acme.test"},
            headers={"X-API-Key": "acmekey"},
        )
        assert r.status_code == 201
        acme_domain_id = r.json()["id"]

        # Globex sees an empty allowlist (no leakage of Acme rules).
        r = c.get("/invite-domains", headers={"X-API-Key": "globexkey"})
        assert r.status_code == 200
        assert r.json() == {"enforcing": False, "domains": []}

        # Globex can invite a gmail address because Globex has no rules.
        r = c.post(
            "/members/invite",
            json={"email": "carol@gmail.test", "role": "writer"},
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 201, r.text

        # Globex cannot delete an Acme rule by id (404, not 204).
        r = c.delete(
            f"/invite-domains/{acme_domain_id}",
            headers={"X-API-Key": "globexkey"},
        )
        assert r.status_code == 404

        # Acme's rule is still in place.
        r = c.get("/invite-domains", headers={"X-API-Key": "acmekey"})
        assert r.status_code == 200
        assert r.json()["enforcing"] is True
        assert any(d["id"] == acme_domain_id for d in r.json()["domains"])
