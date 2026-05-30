"""Static checks on the runtime container images.

These tests do not require a docker daemon. They parse the Dockerfiles
and assert the security contract the Helm chart relies on: non root
user, matching uid/gid, no writes outside the mounted data volume, and
an explicit init process so SIGTERM is forwarded to uvicorn for clean
shutdown under Kubernetes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CPU_DOCKERFILE = REPO_ROOT / "infra" / "docker" / "Dockerfile"
CUDA_DOCKERFILE = REPO_ROOT / "infra" / "docker" / "Dockerfile.cuda"
HELM_VALUES = REPO_ROOT / "infra" / "helm" / "clawhum" / "values.yaml"

# The Helm chart pins this uid/gid via podSecurityContext. The container
# images must match or the pod fails to start with readOnlyRootFilesystem
# + runAsNonRoot enforced.
EXPECTED_UID = "10001"
EXPECTED_GID = "10001"


def _user_directives(dockerfile: Path) -> list[str]:
    out: list[str] = []
    for raw in dockerfile.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#") or not line:
            continue
        if line.split(" ", 1)[0].upper() == "USER":
            out.append(line.split(" ", 1)[1].strip())
    return out


@pytest.mark.parametrize("dockerfile", [CPU_DOCKERFILE, CUDA_DOCKERFILE])
def test_runtime_image_runs_as_nonroot(dockerfile: Path) -> None:
    users = _user_directives(dockerfile)
    assert users, f"{dockerfile.name} is missing a USER directive; pod will fail runAsNonRoot"
    last = users[-1]
    assert last == f"{EXPECTED_UID}:{EXPECTED_GID}", (
        f"{dockerfile.name} final USER is {last!r}; expected "
        f"{EXPECTED_UID}:{EXPECTED_GID} to match Helm podSecurityContext"
    )


@pytest.mark.parametrize("dockerfile", [CPU_DOCKERFILE, CUDA_DOCKERFILE])
def test_runtime_image_creates_data_dir_owned_by_app_user(dockerfile: Path) -> None:
    text = dockerfile.read_text()
    assert "install -d -o 10001 -g 10001" in text and "/app/data" in text, (
        f"{dockerfile.name} must pre-create /app/data owned by 10001:10001 "
        "so the app can start with readOnlyRootFilesystem and no PVC mounted"
    )


@pytest.mark.parametrize("dockerfile", [CPU_DOCKERFILE, CUDA_DOCKERFILE])
def test_runtime_image_uses_tini_as_pid1(dockerfile: Path) -> None:
    text = dockerfile.read_text()
    assert "tini" in text, (
        f"{dockerfile.name} should install tini and use it as ENTRYPOINT "
        "so SIGTERM from Kubernetes reaches uvicorn for graceful shutdown"
    )
    assert 'ENTRYPOINT ["/usr/bin/tini"' in text, (
        f"{dockerfile.name} must set tini as ENTRYPOINT, not just install it"
    )


def test_helm_values_still_pin_expected_uid() -> None:
    """Guard against the chart drifting away from the uid baked into the
    container image. If someone bumps runAsUser they must also rebuild
    the image."""
    text = HELM_VALUES.read_text()
    assert f"runAsUser: {EXPECTED_UID}" in text
    assert f"runAsGroup: {EXPECTED_GID}" in text
