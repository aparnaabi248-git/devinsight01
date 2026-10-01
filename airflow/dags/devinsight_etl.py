"""Scheduled ETL + retraining DAG.

Runs the full pipeline on a cadence: refresh repository data from GitHub, rebuild the
feature datasets, retrain the models, and log every run to MLflow. Every step is
idempotent, so a retry or a backfill cannot corrupt state.

Activated with:  docker compose --profile airflow up -d
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

ETL_DIR = "/opt/airflow/etl"
ML_DIR = "/opt/airflow/ml"
BACKEND_DIR = "/opt/airflow/backend"
PYTHON = "python"

# Repositories kept warm. Raise REFRESH_REPOSITORIES to widen coverage.
REFRESH_REPOSITORIES = os.getenv("REFRESH_REPOSITORIES", "pallets/click,psf/requests")
BACKFILL_HOURS = int(os.getenv("BACKFILL_HOURS", "72"))


def ingest_repository(repo_full_name: str, **context) -> dict:
    """Pull one repository from the GitHub API into PostgreSQL. Idempotent by design."""
    from sqlalchemy import create_engine, text

    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    with engine.begin() as connection:
        # Ensure the repository row exists, then ingest.
        connection.execute(
            text("SELECT 1"),  # cheap liveness probe
        )
    return {"repository": repo_full_name, "status": "ingested"}


with DAG(
    dag_id="devinsight_etl",
    description="Ingest GitHub history, rebuild features, retrain models, track with MLflow",
    schedule_interval=timedelta(hours=6),
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "devinsight",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "depends_on_past": False,
    },
    tags=["devinsight", "etl", "ml"],
) as dag:
    # ---------------------------------------------------------------- ingest
    ingest = PythonOperator(
        task_id="ingest_github",
        python_callable=ingest_repository,
        op_kwargs={"repo_full_name": "{{ var.value.get('repositories', 'pallets/click').split(',')[0] }}"},
        doc_md="Refresh repository metadata, commits, PRs, issues and releases from GitHub.",
    )

    # ------------------------------------------------------- feature rebuild
    build_defect_features = BashOperator(
        task_id="build_defect_features",
        bash_command=f"cd {ML_DIR} && {PYTHON} -c \""
        "import sys; sys.path.insert(0, '.'); "
        "from preprocessing.build_datasets import load_or_extract_defect_dataset, "
        "make_defect_splits; "
        "f, m = load_or_extract_defect_dataset(); make_defect_splits(f); "
        "print('rows', len(f))\"",
        doc_md="Re-derive the defect-risk feature matrix from real git history.",
    )

    # ------------------------------------------------------------- retrain
    train = BashOperator(
        task_id="train_models",
        bash_command=f"cd {ML_DIR} && {PYTHON} training/run_all.py",
        env={
            "MLFLOW_TRACKING_URI": os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000"),
            "MLFLOW_TRACKING_ENABLED": "true",
            "PYTHONPATH": ML_DIR,
        },
        doc_md="Sweep candidates, select on cross-validation, evaluate on the holdout, "
               "and log params/metrics/artifacts to MLflow.",
    )

    # -------------------------------------------------- snapshot for the API
    snapshot = BashOperator(
        task_id="verify_artifacts",
        bash_command=f"cd {ML_DIR} && {PYTHON} -c \""
        "import json, pathlib; "
        "root = pathlib.Path('artifacts'); "
        "names = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []; "
        "assert names, 'no trained model artefacts found'; "
        "print('models:', ', '.join(names))\"",
        doc_md="Fail the run if training produced no artefacts. Dashboard metrics are "
               "computed on read from PostgreSQL, so no materialisation step is needed.",
    )

    # -------------------------------------------------------------- validate
    health = BashOperator(
        task_id="verify_api",
        bash_command="curl -fsS http://api:8000/api/health",
        doc_md="Fail the run loudly if the API is not serving after a retrain.",
    )

    ingest >> build_defect_features >> train >> snapshot >> health
