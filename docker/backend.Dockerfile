# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# DevInsight API — multi-stage build.
# Stage 1 installs Python wheels; stage 2 ships only the runtime + site-packages.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git \
 && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/ml \
    DEVINSIGHT_ROOT=/app

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 devinsight

WORKDIR /app

COPY --from=builder /install /usr/local

# Application code.
COPY backend/app /app/app
COPY backend/alembic /app/alembic
COPY backend/alembic.ini /app/alembic.ini
COPY backend/scripts /app/scripts
COPY --chmod=755 docker/render-start.sh /usr/local/bin/render-start.sh

# The ML package is imported at runtime by the inference service. This also
# brings `ml/artifacts/` (the four trained models) when they exist.
# There is deliberately no separate `COPY ml/artifacts`: that directory is
# gitignored, so on a fresh clone the step would fail and take the documented
# `docker compose up -d --build` down with it. Models are optional here - the
# API starts and reports an empty `models_loaded` until they are trained.
COPY ml /app/ml
# Trained artefacts + the raw/processed data dirs the ETL writes into.
# `data/.gitkeep` is what keeps this path present after a clone; everything
# else under data/ is gitignored.
COPY data /app/data

RUN mkdir -p /app/data/raw/_http_cache /app/data/processed /app/data/features \
 && chown -R devinsight:devinsight /app

USER devinsight
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
