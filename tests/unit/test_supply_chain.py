"""Structural checks for supply-chain hardening workflows.

These tests do not execute GitHub Actions. They parse the workflow YAML
and assert the contract we rely on for shipping verifiable images:

- A dedicated `supply-chain` workflow exists and runs on pushes, PRs,
  a weekly schedule, and manual dispatch. It runs dependency audit,
  source security scan, and container image scan plus SBOM.
- The `docker` publish workflow signs the published digest with cosign
  keyless and emits build provenance attestations.

If any of these guarantees regress, the test fails and we notice before
an unverified image lands in the registry.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_supply_chain_workflow_exists_and_triggers():
    wf = _load("supply-chain.yml")
    assert wf["name"] == "supply-chain"
    # PyYAML parses the bare `on:` key as the boolean True. Accept both.
    triggers = wf.get("on") or wf.get(True)
    assert triggers is not None, "workflow must declare triggers"
    assert "push" in triggers
    assert "pull_request" in triggers
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    # Concurrency guard prevents stacked runs on the same ref.
    assert "concurrency" in wf
    assert wf["concurrency"]["cancel-in-progress"] is True


def test_supply_chain_workflow_has_required_jobs():
    wf = _load("supply-chain.yml")
    jobs = wf["jobs"]
    for required in ("deps-audit", "source-scan", "image-scan"):
        assert required in jobs, f"missing job: {required}"

    # Jobs that produce SARIF must be allowed to upload it.
    for job_name in ("source-scan", "image-scan"):
        perms = jobs[job_name].get("permissions", {})
        assert perms.get("security-events") == "write", (
            f"{job_name} needs security-events: write to upload SARIF"
        )


def test_image_scan_runs_trivy_and_emits_sbom():
    wf = _load("supply-chain.yml")
    steps = wf["jobs"]["image-scan"]["steps"]
    uses = [s.get("uses", "") for s in steps]
    assert any(u.startswith("aquasecurity/trivy-action@") for u in uses), (
        "image-scan must run Trivy"
    )
    assert any(u.startswith("anchore/sbom-action@") for u in uses), (
        "image-scan must generate an SBOM"
    )
    # SARIF upload is what surfaces findings in the GH Security tab.
    assert any(u.startswith("github/codeql-action/upload-sarif@") for u in uses), (
        "image-scan must upload Trivy SARIF"
    )


def test_deps_audit_uses_pip_audit():
    wf = _load("supply-chain.yml")
    steps = wf["jobs"]["deps-audit"]["steps"]
    blob = "\n".join(s.get("run", "") for s in steps if "run" in s)
    assert "pip-audit" in blob, "deps-audit must invoke pip-audit"


def test_source_scan_runs_bandit_with_sarif():
    wf = _load("supply-chain.yml")
    steps = wf["jobs"]["source-scan"]["steps"]
    blob = "\n".join(s.get("run", "") for s in steps if "run" in s)
    assert "bandit" in blob
    assert "sarif" in blob, "bandit must emit SARIF for the Security tab"


def test_docker_publish_signs_and_attests():
    wf = _load("docker.yml")
    job = wf["jobs"]["build"]
    perms = job["permissions"]
    # OIDC token is required for keyless cosign and attest actions.
    assert perms.get("id-token") == "write"
    assert perms.get("attestations") == "write"

    uses = [s.get("uses", "") for s in job["steps"]]
    assert any(u.startswith("sigstore/cosign-installer@") for u in uses), (
        "docker workflow must install cosign"
    )
    assert any(u.startswith("actions/attest-build-provenance@") for u in uses), (
        "docker workflow must emit build provenance attestations"
    )

    # SBOM and provenance must be baked into the build output.
    build_step = next(
        s for s in job["steps"] if s.get("uses", "").startswith("docker/build-push-action@")
    )
    assert build_step["with"].get("sbom") is True
    assert "provenance" in build_step["with"]

    # The cosign sign step must reference the immutable digest, not a
    # mutable tag, so the signature binds to the exact published bytes.
    sign_step = next(
        s for s in job["steps"] if "cosign sign" in (s.get("run", "") or "")
    )
    assert "${DIGEST}" in sign_step["run"]
