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
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY packages /app/packages
COPY services /app/services
COPY cli /app/cli
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/packages/core:/app/packages/audio:/app/packages/embed:/app/packages/index:/app/packages/match:/app/packages/library:/app/services/api:/app/services/indexer:/app/cli
EXPOSE 7451
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:7451/health',timeout=3).status==200 else 1)"
CMD ["uvicorn", "clawhum_api.app:app", "--host", "0.0.0.0", "--port", "7451"]
