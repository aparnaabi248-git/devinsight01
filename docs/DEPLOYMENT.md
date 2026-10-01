# Deployment Runbook

## 1. Target topology

Production runs the same four services as local development, with images pulled from a
registry rather than built on the host:

```
             ┌──────────────┐
   users ───▶│  nginx / CDN │  TLS termination, static assets
             └──────┬───────┘
                    │  /api
             ┌──────▼───────┐        ┌──────────────┐
             │  api (uvicorn)├───────▶│  PostgreSQL  │
             └──────┬───────┘  :5432 │      16      │
                    │ :5000          └──────────────┘
             ┌──────▼───────┐
             │   MLflow     │   experiment + artifact store (file volume)
             └──────────────┘
```

The compose file in this repository is the reference deployment. For a larger install,
run the images directly on a managed platform (ECS, Cloud Run, Render, Fly) and point
`DATABASE_URL` and `MLFLOW_TRACKING_URI` at managed services.

## 2. First-time server setup

```bash
# --- base OS and Docker -------------------------------------------------
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" && newgrp docker

# --- application --------------------------------------------------------
sudo mkdir -p /opt/devinsight && sudo chown "$USER" /opt/devinsight
git clone <your-repo> /opt/devinsight/devinsight
cd /opt/devinsight/devinsight
```

## 3. Secrets

Every secret comes from the environment. Never bake one into an image or commit one.

```bash
# .env on the host — chmod 600, owned by the deploy user
cd /opt/devinsight/devinsight
cp .env.example .env
chmod 600 .env
echo "SECRET_KEY=$(openssl rand -hex 32)" >> .env
echo "GITHUB_TOKEN=ghp_xxx" >> .env
```

| Variable | How to obtain | Notes |
|---|---|---|
| `SECRET_KEY` | `openssl rand -hex 32` | The API **refuses to start** in production with the dev key |
| `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Fine-grained tokens | Read-only public-repo access. 5,000 req/hr vs 60 anonymous |
| `POSTGRES_PASSWORD` | `openssl rand -base64 32` | Or use the managed instance's credential |
| `AIRFLOW_FERNET_KEY` | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` | Required if Airflow holds connections |

Never expose `GITHUB_TOKEN` to the browser. The frontend bundle contains only a URL.

## 4. Database

### Migrations

Migrations are versioned and additive, so they are safe to run on every deploy:

```bash
docker compose run --rm api alembic upgrade head
```

Verify the live schema still matches the ORM (the same gate CI runs):

```bash
docker compose run --rm api python scripts/check_schema.py
```

### Backup

```bash
# nightly logical backup
docker compose exec -T db pg_dump -U devinsight -Fc devinsight \
  | gzip > /var/backups/devinsight-$(date +%F).dump.gz

# restore
gunzip -c /var/backups/devinsight-2025-06-01.dump.gz \
  | docker compose exec -T db pg_restore -U devinsight -d devinsight --clean --if-exists
```

Point-in-in-time recovery: enable WAL archiving on the managed instance, or run
`pgBackRest` for a self-hosted PostgreSQL.

## 5. First release

```bash
cd /opt/devinsight/devinsight
docker compose pull
docker compose up -d
docker compose ps                       # wait for db/api/web to report healthy
curl -fsS http://localhost:8000/api/health | jq
```

Then train the models once (they are git-ignored artefacts, not source):

```bash
docker compose exec api python ml/training/run_all.py
docker compose exec -T api python /app/tests/test_artifacts.py 2>/dev/null || true
```

Confirm the ML endpoints respond:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"ops@example.com","username":"ops","password":"OpsPass123"}' \
  | jq -r .access_token)

curl -s -X POST http://localhost:8000/api/ml/defect-risk \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"repository":"pallets/click","changes":[{"path":"src/click/core.py","additions":180,"deletions":40}]}' | jq
```

## 6. Rollout

`docker-compose.yml` uses named volumes, so a rollout replaces containers without losing
the ETL raw store, the trained artefacts, the reports or the MLflow store.

```bash
cd /opt/devinsight/devinsight
git pull --ff-only
docker compose run --rm api alembic upgrade head
docker compose pull
docker compose up -d --remove-orphans
docker image prune -f
curl -fsS http://localhost:8000/api/health
```

The automated path: push a `v*` tag and let `.github/workflows/deploy.yml` do this over
SSH, gated on a health check.

### Zero-downtime notes

The API is stateless, so it scales horizontally behind a load balancer. The compose file
uses `docker compose up -d`, which restarts containers in place — fine for a single node.
For true zero downtime, run two API replicas and drain one at a time.

## 7. Scaling

| Symptom | Action |
|---|---|
| API CPU-bound | Scale API replicas; it is stateless |
| DB CPU on dashboard queries | Add a read replica; point analytics reads at it |
| Ingestion saturating workers | Move ingestion to Celery/arq; today it runs in FastAPI background tasks |
| ETag cache not shared | Add Redis and replace the disk cache in `services/github_client.py` |
| `commits` table very large | Range-partition on `authored_at` (see `docs/DATABASE.md` §4) |
| Slow trend queries | Pre-materialise `analytics_snapshots` per day instead of scanning facts |

## 8. Monitoring

| Signal | Where | Action on breach |
|---|---|---|
| `/api/health` returns `unhealthy` | container health check | Restart the API; inspect `docker compose logs api` |
| `status: degraded` | `models_loaded` empty | Retrain: `python ml/training/run_all.py` |
| API `429` from GitHub | `GET /api/health/github` | Add `GITHUB_TOKEN` or lower ingest volume |
| Job `status: failed` | `GET /api/jobs` | Read `error`; usually a transient GitHub failure |
| Error rate up | reverse-proxy logs | Roll back with the previous image tag |

Ship logs to your aggregator with `LOG_JSON=true` (the logger already emits structured,
redacted JSON).

## 9. Backup and recovery targets

| Data | Method | RPO | RTO |
|---|---|---|---|
| PostgreSQL | `pg_dump` nightly + WAL archiving | 24 h (1 h with WAL) | < 1 h |
| Trained artefacts | Baked into the image or synced to object storage | Deploy cycle | Rebuild in ~10 min |
| MLflow store | File volume snapshot → object storage | 24 h | < 1 h |
| Raw ETL data | Disposable — re-cloneable with `acquire.py` | n/a | Minutes |

## 10. Security checklist

- [ ] `SECRET_KEY` is a fresh 32-byte random value, not the template default
- [ ] `GITHUB_TOKEN` is fine-grained and read-only
- [ ] `.env` is `chmod 600` and never committed
- [ ] TLS terminated at the proxy; API not exposed directly to the internet
- [ ] `CORS_ORIGINS` lists only your real frontend origins
- [ ] `docker-compose.yml` `SECRET_KEY: ${SECRET_KEY:?...}` guard left in place
- [ ] Database backups verified by an actual restore test
- [ ] CI's secret-scan job passing on every push
