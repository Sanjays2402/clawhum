"""Sanity tests for the Helm chart under infra/helm/clawhum.

Helm CLI is not available in CI for this repo, so these tests parse the
chart files directly and assert structural invariants we care about:

- values.yaml is valid YAML and exposes the enterprise knobs
  (autoscaling, podDisruptionBudget, networkPolicy, security contexts,
  serviceAccount).
- Every template file is a non-empty text file (Helm-templated YAML, so
  not parseable as raw YAML without rendering).
- Specific templates carry the expected top-level kinds.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[2] / "infra" / "helm" / "clawhum"
TEMPLATES = CHART_DIR / "templates"


def test_chart_yaml_is_well_formed():
    chart = yaml.safe_load((CHART_DIR / "Chart.yaml").read_text())
    assert chart["name"] == "clawhum"
    assert chart["apiVersion"] == "v2"
    assert "version" in chart


def test_values_yaml_exposes_enterprise_knobs():
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text())

    # ServiceAccount
    sa = values["serviceAccount"]
    assert sa["create"] is True
    assert "annotations" in sa

    # Pod / container security contexts hardened by default.
    pod_sc = values["podSecurityContext"]
    assert pod_sc["runAsNonRoot"] is True
    assert pod_sc["runAsUser"] >= 1000
    assert pod_sc["seccompProfile"]["type"] == "RuntimeDefault"

    csc = values["containerSecurityContext"]
    assert csc["allowPrivilegeEscalation"] is False
    assert csc["readOnlyRootFilesystem"] is True
    assert csc["runAsNonRoot"] is True
    assert "ALL" in csc["capabilities"]["drop"]

    # Autoscaling defined with sane defaults.
    hpa = values["autoscaling"]
    assert "enabled" in hpa
    assert hpa["minReplicas"] >= 1
    assert hpa["maxReplicas"] >= hpa["minReplicas"]
    assert 1 <= hpa["targetCPUUtilizationPercentage"] <= 100

    # PDB on by default.
    pdb = values["podDisruptionBudget"]
    assert pdb["enabled"] is True
    assert pdb["minAvailable"] >= 1

    # NetworkPolicy knobs present (off by default is fine).
    np = values["networkPolicy"]
    assert "enabled" in np
    assert "egressCIDRs" in np

    # Resource limits still present.
    res = values["resources"]
    assert "requests" in res and "limits" in res


@pytest.mark.parametrize(
    "filename,expected_kind",
    [
        ("deployment.yaml", "Deployment"),
        ("service.yaml", "Service"),
        ("secret.yaml", "Secret"),
        ("hpa.yaml", "HorizontalPodAutoscaler"),
        ("pdb.yaml", "PodDisruptionBudget"),
        ("networkpolicy.yaml", "NetworkPolicy"),
        ("serviceaccount.yaml", "ServiceAccount"),
    ],
)
def test_template_declares_expected_kind(filename: str, expected_kind: str):
    body = (TEMPLATES / filename).read_text()
    assert re.search(rf"^kind:\s*{expected_kind}\s*$", body, re.MULTILINE), (
        f"{filename} missing kind: {expected_kind}"
    )


def test_deployment_template_pins_security_context_and_probes():
    body = (TEMPLATES / "deployment.yaml").read_text()
    # Security context block must be wired from values, not hardcoded.
    assert ".Values.podSecurityContext" in body
    assert ".Values.containerSecurityContext" in body
    # ServiceAccount usage.
    assert "serviceAccountName" in body
    # Probes still present.
    assert "readinessProbe" in body
    assert "livenessProbe" in body
    # Replicas only set when HPA is off.
    assert "if not .Values.autoscaling.enabled" in body


def test_hpa_template_is_gated_by_autoscaling_enabled():
    body = (TEMPLATES / "hpa.yaml").read_text()
    assert body.lstrip().startswith("{{- if .Values.autoscaling.enabled")
    assert "autoscaling/v2" in body


def test_pdb_template_requires_replicas_gt_one():
    body = (TEMPLATES / "pdb.yaml").read_text()
    assert "gt (int .Values.replicaCount) 1" in body
    assert "policy/v1" in body


def test_networkpolicy_allows_dns_and_app_port():
    body = (TEMPLATES / "networkpolicy.yaml").read_text()
    assert "kube-dns" in body
    assert "port: 7451" in body
    assert "policyTypes" in body
