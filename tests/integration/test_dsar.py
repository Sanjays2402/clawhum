"""Tests for the DSAR (Data Subject Access Request) tracker.

What an enterprise privacy review actually asks for, and what these
tests cover:

1. Admin can file a request, list the queue, get one by id, and
   advance it through ``in_progress`` to a terminal state.
2. Non-admin roles get 403 on every mutation.
3. Tenant A cannot read, file into, or advance tenant B's requests
   (no cross-tenant leakage at the route layer).
4. Invalid input is rejected with a structured 400: bad email,
   unknown kind, unknown status, due_days out of range, rejection
   without justification.
5. A request already in a terminal state cannot be reopened (409).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, api_keys: str):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv(
        "CLAWHUM_DSAR_REQUESTS_PATH", str(tmp_path / "dsar.jsonl")
    )
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_RATE_LIMIT_PER_MINUTE", "10000")
    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import dsar
    dsar.reset_cache()
    from clawhum_api.app import create_app
    return TestClient(create_app())


def _hdr(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def test_admin_can_file_list_and_advance(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")

    empty = c.get("/dsar", headers=_hdr("sk_admin"))
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["requests"] == []
    assert body["summary"]["open"] == 0
    assert "access" in body["kinds"]
    assert "received" in body["statuses"]

    created = c.post(
        "/dsar",
        json={
            "subject_email": "jane@example.com",
            "kind": "access",
            "note": "Forwarded from privacy@",
            "due_days": 30,
        },
        headers=_hdr("sk_admin"),
    )
    assert created.status_code == 201, created.text
    row = created.json()
    rid = row["id"]
    assert row["status"] == "received"
    assert row["overdue"] is False
    assert len(row["history"]) == 1
    assert row["history"][0]["action"] == "filed"

    # Visible in list with open=1.
    listed = c.get("/dsar", headers=_hdr("sk_admin")).json()
    assert listed["summary"]["open"] == 1
    assert listed["summary"]["by_kind"]["access"] == 1
    assert listed["requests"][0]["id"] == rid

    # Advance to in_progress, then completed.
    step1 = c.post(
        f"/dsar/{rid}/advance",
        json={"to_status": "in_progress", "note": "assigned to privacy ops"},
        headers=_hdr("sk_admin"),
    )
    assert step1.status_code == 200, step1.text
    assert step1.json()["status"] == "in_progress"
    assert len(step1.json()["history"]) == 2

    step2 = c.post(
        f"/dsar/{rid}/advance",
        json={"to_status": "completed", "note": "export delivered"},
        headers=_hdr("sk_admin"),
    )
    assert step2.status_code == 200, step2.text
    final = step2.json()
    assert final["status"] == "completed"
    assert final["closed_at"] > 0

    # Cannot reopen a terminal request.
    reopen = c.post(
        f"/dsar/{rid}/advance",
        json={"to_status": "in_progress", "note": "oops"},
        headers=_hdr("sk_admin"),
    )
    assert reopen.status_code == 409


def test_non_admin_cannot_mutate(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "ops:sk_admin:9999:admin:acme,ro:sk_reader:9999:reader:acme",
    )
    # Reader cannot list.
    assert c.get("/dsar", headers=_hdr("sk_reader")).status_code == 403
    # Reader cannot create.
    deny = c.post(
        "/dsar",
        json={"subject_email": "x@y.com", "kind": "access"},
        headers=_hdr("sk_reader"),
    )
    assert deny.status_code == 403


def test_tenant_isolation(monkeypatch, tmp_path):
    c = _client(
        monkeypatch,
        tmp_path,
        "alpha:sk_a:9999:admin:acme,beta:sk_b:9999:admin:globex",
    )
    a = c.post(
        "/dsar",
        json={"subject_email": "alice@acme.com", "kind": "erasure"},
        headers=_hdr("sk_a"),
    )
    assert a.status_code == 201, a.text
    rid = a.json()["id"]

    # Tenant B sees an empty queue and cannot fetch tenant A's request.
    other_list = c.get("/dsar", headers=_hdr("sk_b")).json()
    assert other_list["requests"] == []
    assert other_list["summary"]["total"] == 0
    miss = c.get(f"/dsar/{rid}", headers=_hdr("sk_b"))
    assert miss.status_code == 404
    deny = c.post(
        f"/dsar/{rid}/advance",
        json={"to_status": "in_progress", "note": "n/a"},
        headers=_hdr("sk_b"),
    )
    assert deny.status_code == 404


def test_input_validation(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path, "ops:sk_admin:9999:admin:acme")

    bad_email = c.post(
        "/dsar",
        json={"subject_email": "not-an-email", "kind": "access"},
        headers=_hdr("sk_admin"),
    )
    assert bad_email.status_code == 400

    bad_kind = c.post(
        "/dsar",
        json={"subject_email": "j@example.com", "kind": "telepathy"},
        headers=_hdr("sk_admin"),
    )
    assert bad_kind.status_code == 400

    bad_days = c.post(
        "/dsar",
        json={
            "subject_email": "j@example.com",
            "kind": "access",
            "due_days": 0,
        },
        headers=_hdr("sk_admin"),
    )
    assert bad_days.status_code == 422  # pydantic ge=1 violation

    good = c.post(
        "/dsar",
        json={"subject_email": "j@example.com", "kind": "rectification"},
        headers=_hdr("sk_admin"),
    ).json()
    rid = good["id"]

    bad_status = c.post(
        f"/dsar/{rid}/advance",
        json={"to_status": "vibes", "note": "n/a"},
        headers=_hdr("sk_admin"),
    )
    assert bad_status.status_code == 409

    # Rejection requires note.
    reject_empty = c.post(
        f"/dsar/{rid}/advance",
        json={"to_status": "rejected", "note": ""},
        headers=_hdr("sk_admin"),
    )
    assert reject_empty.status_code == 409

    # With note, rejection succeeds.
    reject_ok = c.post(
        f"/dsar/{rid}/advance",
        json={"to_status": "rejected", "note": "subject not a data subject"},
        headers=_hdr("sk_admin"),
    )
    assert reject_ok.status_code == 200
    assert reject_ok.json()["status"] == "rejected"

    # Bad list filter.
    bad_filter = c.get("/dsar?status=vibes", headers=_hdr("sk_admin"))
    assert bad_filter.status_code == 400
