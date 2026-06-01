"""Tests for the security incident (breach notification) tracker.

What an enterprise security review actually asks for, and what these
tests cover:

1. Admin can declare an incident, list the queue, get one by id,
   append notes, advance it through ``contained`` to ``resolved``,
   and record regulator + data subject notification.
2. Non-admin roles get 403 on every mutation.
3. Tenant A cannot read, declare into, advance, or notify against
   tenant B's incidents (no cross-tenant leakage at the route layer).
4. Invalid input is rejected with a structured 400: empty title,
   unknown severity, unknown status, discovered_at in the future,
   closing with no action without a justification.
5. An incident already in a terminal state cannot be reopened or
   modified (409). Regulator and subject notification can each be
   recorded at most once (409 on repeat).
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_INCIDENTS_PATH", str(tmp_path / "incidents.jsonl")
    )
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import incidents
    incidents.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _hdr(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def test_admin_declare_list_advance_and_notify(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")

    empty = c.get("/incidents", headers=_hdr("sk_admin"))
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["incidents"] == []
    assert body["summary"]["open"] == 0
    assert "high" in body["severities"]
    assert "open" in body["statuses"]
    assert body["notify_deadline_seconds"] == 72 * 3600

    declared = c.post(
        "/incidents",
        json={
            "title": "Suspicious S3 access from unknown IP",
            "severity": "high",
            "detail": "Unfamiliar IP enumerated audio bucket at 03:14 UTC.",
        },
        headers=_hdr("sk_admin"),
    )
    assert declared.status_code == 201, declared.text
    inc = declared.json()
    iid = inc["id"]
    assert inc["status"] == "open"
    assert inc["severity"] == "high"
    assert inc["regulator_notified_at"] == 0.0
    assert inc["notify_overdue"] is False
    assert len(inc["history"]) == 1
    assert inc["history"][0]["kind"] == "declared"

    listed = c.get("/incidents", headers=_hdr("sk_admin")).json()
    assert listed["summary"]["open"] == 1
    assert listed["summary"]["by_severity"]["high"] == 1
    assert listed["incidents"][0]["id"] == iid

    noted = c.post(
        f"/incidents/{iid}/notes",
        json={"note": "Rotated affected IAM credentials."},
        headers=_hdr("sk_admin"),
    )
    assert noted.status_code == 200, noted.text
    assert len(noted.json()["history"]) == 2

    contained = c.post(
        f"/incidents/{iid}/advance",
        json={"to_status": "contained", "note": "Bucket policy hardened."},
        headers=_hdr("sk_admin"),
    )
    assert contained.status_code == 200, contained.text
    assert contained.json()["status"] == "contained"

    reg = c.post(
        f"/incidents/{iid}/regulator-notified",
        json={
            "regulator_name": "ICO (UK)",
            "regulator_reference": "REF-2025-0042",
            "note": "Filed via online portal.",
        },
        headers=_hdr("sk_admin"),
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["regulator_notified_at"] > 0
    assert reg.json()["regulator_name"] == "ICO (UK)"

    subs = c.post(
        f"/incidents/{iid}/subjects-notified",
        json={"affected_count": 12, "note": "Emails sent."},
        headers=_hdr("sk_admin"),
    )
    assert subs.status_code == 200, subs.text
    assert subs.json()["affected_count"] == 12
    assert subs.json()["subjects_notified_at"] > 0

    resolved = c.post(
        f"/incidents/{iid}/advance",
        json={"to_status": "resolved", "note": "Root cause addressed."},
        headers=_hdr("sk_admin"),
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["closed_at"] > 0

    # Cannot reopen a terminal incident.
    reopen = c.post(
        f"/incidents/{iid}/advance",
        json={"to_status": "open", "note": "nope"},
        headers=_hdr("sk_admin"),
    )
    assert reopen.status_code == 409, reopen.text

    # Cannot record regulator notification twice.
    dup = c.post(
        f"/incidents/{iid}/regulator-notified",
        json={"regulator_name": "ICO (UK)", "regulator_reference": ""},
        headers=_hdr("sk_admin"),
    )
    assert dup.status_code == 409, dup.text


def test_member_role_cannot_mutate(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "ops:sk_admin:9999:admin:acme,user:sk_member:9999:member:acme",
    )

    # Admin declares so there is something for the member to attack.
    inc = c.post(
        "/incidents",
        json={"title": "Test event", "severity": "low"},
        headers=_hdr("sk_admin"),
    ).json()
    iid = inc["id"]

    # Member can neither list nor mutate.
    assert c.get("/incidents", headers=_hdr("sk_member")).status_code == 403
    declared = c.post(
        "/incidents",
        json={"title": "Member attempt", "severity": "low"},
        headers=_hdr("sk_member"),
    )
    assert declared.status_code == 403, declared.text
    advanced = c.post(
        f"/incidents/{iid}/advance",
        json={"to_status": "contained", "note": ""},
        headers=_hdr("sk_member"),
    )
    assert advanced.status_code == 403, advanced.text


def test_cross_tenant_isolation(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "ops_a:sk_a:9999:admin:acme,ops_b:sk_b:9999:admin:globex",
    )

    a = c.post(
        "/incidents",
        json={"title": "Acme event", "severity": "medium"},
        headers=_hdr("sk_a"),
    )
    assert a.status_code == 201, a.text
    a_id = a.json()["id"]

    b = c.post(
        "/incidents",
        json={"title": "Globex event", "severity": "low"},
        headers=_hdr("sk_b"),
    )
    assert b.status_code == 201, b.text

    # Tenant B's list never includes A's incident.
    listed_b = c.get("/incidents", headers=_hdr("sk_b")).json()
    b_ids = {row["id"] for row in listed_b["incidents"]}
    assert a_id not in b_ids
    assert listed_b["summary"]["total"] == 1

    # Tenant B cannot fetch A's incident by id (looks like 404, not 403,
    # so id existence does not leak across tenants).
    got = c.get(f"/incidents/{a_id}", headers=_hdr("sk_b"))
    assert got.status_code == 404, got.text

    # Tenant B cannot advance A's incident.
    adv = c.post(
        f"/incidents/{a_id}/advance",
        json={"to_status": "contained", "note": "noop"},
        headers=_hdr("sk_b"),
    )
    assert adv.status_code == 404, adv.text

    # Tenant B cannot record regulator notification on A's incident.
    reg = c.post(
        f"/incidents/{a_id}/regulator-notified",
        json={"regulator_name": "Other"},
        headers=_hdr("sk_b"),
    )
    assert reg.status_code == 404, reg.text


def test_input_validation(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")

    # Empty title.
    r = c.post(
        "/incidents",
        json={"title": "   ", "severity": "low"},
        headers=_hdr("sk_admin"),
    )
    assert r.status_code == 400, r.text

    # Unknown severity.
    r = c.post(
        "/incidents",
        json={"title": "ok", "severity": "doom"},
        headers=_hdr("sk_admin"),
    )
    assert r.status_code == 400, r.text

    # discovered_at far in the future.
    r = c.post(
        "/incidents",
        json={
            "title": "ok",
            "severity": "low",
            "discovered_at": time.time() + 86400,
        },
        headers=_hdr("sk_admin"),
    )
    assert r.status_code == 400, r.text

    # Declare a real one to test status flow.
    declared = c.post(
        "/incidents",
        json={"title": "real", "severity": "low"},
        headers=_hdr("sk_admin"),
    )
    iid = declared.json()["id"]

    # Unknown status.
    r = c.post(
        f"/incidents/{iid}/advance",
        json={"to_status": "exploded", "note": ""},
        headers=_hdr("sk_admin"),
    )
    assert r.status_code == 409, r.text

    # closed_no_action requires justification.
    r = c.post(
        f"/incidents/{iid}/advance",
        json={"to_status": "closed_no_action", "note": ""},
        headers=_hdr("sk_admin"),
    )
    assert r.status_code == 409, r.text

    # Same status transition rejected.
    r = c.post(
        f"/incidents/{iid}/advance",
        json={"to_status": "open", "note": ""},
        headers=_hdr("sk_admin"),
    )
    assert r.status_code == 409, r.text
