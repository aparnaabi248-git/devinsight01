# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# MLflow tracking server — stores runs, params, metrics and model artifacts.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MLFLOW_TRACKING_URI=http://0.0.0.0:5000

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "mlflow>=2.17" "gunicorn"

# The tracking store lives on a mounted volume.
ENV MLFLOW_BACKEND_STORE_URI=file:///mlflow/mlruns

EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:5000/health')" || exit 1

CMD ["mlflow", "server", \
     "--host", "0.0.0.0", \
     "--port", "5000", \
     "--backend-store-uri", "file:///mlflow/mlruns", \
     "--default-artifact-root", "file:///mlflow/artifacts", \
     "--workers", "2"]
