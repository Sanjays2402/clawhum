# syntax=docker/dockerfile:1.7

ARG BASE=python:3.11-slim
ARG CUDA_BASE=nvidia/cuda:12.4.1-runtime-ubuntu22.04

FROM ${BASE} AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libsndfile1 ffmpeg && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY services ./services
COPY cli ./cli
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir .

FROM ${BASE} AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

# Create a dedicated non-root user matching the uid/gid the Helm chart
# enforces via podSecurityContext (runAsUser=10001, fsGroup=10001) and
# the container securityContext (runAsNonRoot=true, readOnlyRootFilesystem=true).
# Without this USER directive the pod fails to start under the chart's
# default PodSecurity policy.
RUN groupadd --system --gid 10001 clawhum \
 && useradd --system --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin clawhum

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY packages /app/packages
COPY services /app/services
COPY cli /app/cli

# /app/data is the only writable path the app needs at runtime. The
# Helm chart mounts a PVC or emptyDir there, but for plain docker run
# we pre-create it owned by the non-root user so an unmounted run still
# works. Everything else on / is read-only when readOnlyRootFilesystem
# is enforced; /tmp is provided via emptyDir in the chart.
RUN install -d -o 10001 -g 10001 -m 0775 /app/data

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/packages/core:/app/packages/audio:/app/packages/embed:/app/packages/index:/app/packages/match:/app/packages/library:/app/services/api:/app/services/indexer:/app/cli \
    HOME=/app

USER 10001:10001
EXPOSE 7451
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:7451/health',timeout=3).status==200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "clawhum_api.app:app", "--host", "0.0.0.0", "--port", "7451"]
