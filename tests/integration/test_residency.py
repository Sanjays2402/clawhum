"""Workspace data residency enforcement.

A workspace pinned to ``eu`` must be rejected by a node running in
``us`` when it tries to mutate data, while a workspace pinned to the
same region as the node passes through. Read requests are allowed in
any region so dashboards keep working. A sibling tenant that has not
opted in to a pin is unaffected by another tenant's policy.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(
    monkeypatch,
    tmp_path: Path,
    *,
    api_keys: str,
    node_region: str = "us",
    enforcement: bool = True,
):
    monkeypatch.setenv("CLAWHUM_INDEX_PATH", str(tmp_path / "ix.npz"))
    monkeypatch.setenv("CLAWHUM_METADATA_PATH", str(tmp_path / "meta.jsonl"))
    monkeypatch.setenv("CLAWHUM_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CLAWHUM_RESIDENCY_PATH", str(tmp_path / "residency.jsonl"))
    monkeypatch.setenv("CLAWHUM_QUOTA_PATH", str(tmp_path / "quotas.jsonl"))
    monkeypatch.setenv("CLAWHUM_FEEDBACK_PATH", str(tmp_path / "feedback.jsonl"))
    monkeypatch.setenv("CLAWHUM_API_KEY", "")
    monkeypatch.setenv("CLAWHUM_API_KEYS", api_keys)
    monkeypatch.setenv("CLAWHUM_REGION", node_region)
    monkeypatch.setenv(
        "CLAWHUM_RESIDENCY_ENFORCEMENT", "true" if enforcement else "false"
    )

    from clawhum_core.settings import get_settings
    get_settings.cache_clear()
    from clawhum_api.api_keys import reset_registry_cache
    reset_registry_cache()
    from clawhum_api import residency_store
    residency_store.reset_cache()
    from clawhum_api.app import create_app

    return TestClient(create_app())


def test_mutating_request_blocked_when_workspace_pinned_to_other_region(
    monkeypatch, tmp_path
):
    """An EU workspace cannot mutate against a US node when enforce=true."""
    spec = "eu:sk_eu:0:admin|writer:europa,us:sk_us:0:admin|writer:americana"
    with _client(monkeypatch, tmp_path, api_keys=spec, node_region="us") as c:
        from clawhum_api import residency_store

        residency_store.set_(
            tenant_id="europa",
            region="eu",
            enforce=True,
            actor="test",
        )
        # Sibling tenant is unset on purpose; their requests must not be
        # affected by the EU tenant's pin.

        # Read is always allowed even cross-region so audit/dashboards work.
        read = c.get("/me", headers={"x-api-key": "sk_eu"})
        assert read.status_code == 200
        assert read.headers.get("X-Data-Region") == "us"
        assert read.headers.get("X-Workspace-Region") == "eu"

        # Mutating call from the EU workspace against a US node is rejected
        # with 451 Unavailable For Legal Reasons, naming both regions.
        write = c.post(
            "/feedback",
            json={"track_id": "t1", "rating": 5},
            headers={"x-api-key": "sk_eu"},
        )
        assert write.status_code == 451, write.text
        body = write.json()
        assert body["code"] == "residency_mismatch"
        assert body["tenant_region"] == "eu"
        assert body["node_region"] == "us"
        assert write.headers.get("X-Workspace-Region") == "eu"

        # Sibling tenant (no pin) on the same node is unaffected.
        sibling = c.post(
            "/feedback",
            json={"track_id": "t1", "rating": 5},
            headers={"x-api-key": "sk_us"},
        )
        assert sibling.status_code != 451


def test_matching_region_passes_through(monkeypatch, tmp_path):
    """Same-region mutating requests work normally and advertise the region."""
    spec = "eu:sk_eu:0:admin|writer:europa"
    with _client(monkeypatch, tmp_path, api_keys=spec, node_region="eu") as c:
        from clawhum_api import residency_store

        residency_store.set_(
            tenant_id="europa",
            region="eu",
            enforce=True,
            actor="test",
        )
        resp = c.post(
            "/feedback",
            json={"track_id": "t1", "rating": 5},
            headers={"x-api-key": "sk_eu"},
        )
        assert resp.status_code != 451
        assert resp.headers.get("X-Data-Region") == "eu"
        assert resp.headers.get("X-Workspace-Region") == "eu"


def test_enforcement_disabled_globally_lets_request_through(monkeypatch, tmp_path):
    """When the master switch is off, mismatch is logged but not blocked."""
    spec = "eu:sk_eu:0:admin|writer:europa"
    with _client(
        monkeypatch, tmp_path, api_keys=spec, node_region="us", enforcement=False
    ) as c:
        from clawhum_api import residency_store

        residency_store.set_(
            tenant_id="europa",
            region="eu",
            enforce=True,
            actor="test",
        )
        resp = c.post(
            "/feedback",
            json={"track_id": "t1", "rating": 5},
            headers={"x-api-key": "sk_eu"},
        )
        assert resp.status_code != 451
        assert resp.headers.get("X-Data-Region") == "us"
