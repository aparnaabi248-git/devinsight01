# Architecture & Design Specification

> AI-Powered Software Engineering Analytics & Predictive Intelligence Platform

This document is the authoritative design contract for DevInsight. Every phase
implements exactly what is specified here.

---

## 1. Problem Statement

Engineering organisations have rich data — commits, pull requests, issues, releases,
reviews — but almost none of it is turned into decisions. Spreadsheet-based reporting is
slow, historical, and cannot answer forward-looking questions. The questions managers
actually ask are predictive:

- *Will this PR introduce a defect?*
- *What kind of issue is this, so it routes to the right team?*
- *How urgent is this really?*
- *How much effort will resolving this take?*

DevInsight ingests real repository history, engineers features from it, trains and
evaluates machine-learning models, and serves those models behind a REST API and an
interactive dashboard — with full experiment tracking and reproducibility.

The hard part is not the API. It is that **none of these labels exist in the data.**
GitHub does not record whether a change was defective, how urgent an issue truly is, or
how many hours it took. So the real work is deriving defensible proxy targets from
observable history, measuring honestly, and documenting exactly what each proxy cannot
see. See [`ML.md`](ML.md) and `ml/docs/LABELS.md`.

## 2. System Context

```
        ┌────────────┐
        │  Analysts /│
        │  Engineers │  (Browser, HTTPS)
        └─────┬──────┘
              │ JWT Bearer
              ▼
    ┌─────────────────────────┐        ┌──────────────────────────┐
    │  React + TypeScript SPA │◀──────▶│   FastAPI (REST + v1)    │
    │  Vite / Recharts        │        │  routers·services·schemas│
    └─────────────────────────┘        └────┬──────────┬──────────┘
                                             │          │
                        ┌────────────────────┘          └───────────────────┐
                        ▼                                               ▼
              ┌───────────────────┐                        ┌────────────────────┐
              │  PostgreSQL 16    │                        │  GitHub REST API   │
              │  SQLAlchemy 2.0   │                        │  + git transport   │
              │  (system of record)│                       └─────────┬──────────┘
              └─────────┬─────────┘                                  │
                        │                                    ┌─────────▼──────────┐
              ┌─────────▼─────────┐                           │ github_client.py   │
              │  ML Inference Svc  │                           │ retry·backoff·     │
              │  loads joblib from│                           │ ETag·ratelimit·    │
              │  ml/artifacts     │                           │ token pool·cache   │
              └─────────┬─────────┘                           └─────────┬──────────┘
                        │                                               │
        ┌───────────────▼───────────────┐                  ┌────────────▼───────────┐
        │  MLflow Tracking Server :5000 │                  │ Raw Store (immutable) │
        │  params·metrics·artifacts    │                  │ data/raw (JSONL, git) │
        └───────────────┬───────────────┘                  └────────────┬───────────┘
                        │                                                  │
                        └──────────────┬───────────────────────────────────┘
                                       ▼
        ┌──────────────────────────────────────────────────────────────────────┐
        │  ETL Orchestrator (Python)  →  extract ▸ clean ▸ transform ▸          │
        │  features ▸ validate ▸ persist  (scheduled by Airflow / Cron / CLI)  │
        └──────────────────────────────────────────────────────────────────────┘
```

## 3. Technology Decisions

| Layer | Choice | Rationale | Rejected alternative |
|---|---|---|---|
| API | **FastAPI** 0.115+ | Async, Pydantic v2 validation, auto OpenAPI, DI | Flask (no schema/async), Django (monolithic) |
| Validation | **Pydantic v2** | Native speed, `field_validator`, one schema for body+docs | dataclasses (no coercion) |
| ORM | **SQLAlchemy 2.0** typed `DeclarativeBase` | Real relationships, index/constraint control | Raw SQL (no portability) |
| DB | **PostgreSQL 16** | Window functions for trends, JSONB, GIN/FK indexes | SQLite (no prod concurrency) |
| Migrations | **Alembic** | Versioned schema, CI-safe | `create_all` (destructive) |
| GitHub access | **httpx** + `git` CLI | REST for metadata/issues; git transport for full history/diffs | API-only (60 req/hr kills bulk) |
| Cache | **Disk ETag cache** | Zero-infra, survives restarts | Redis (extra service) — later |
| ML | **scikit-learn + XGBoost** | sklearn for the baseline sweep; XGBoost for tabular | end-to-end DL (wrong tool) |
| Tracking | **MLflow** 2.x | Industry standard, UI + registry | W&B (hosted/paid) |
| Serialisation | **joblib** + `manifest.json` | Fast, version-safe, self-describing | pickle (unsafe) |
| Frontend | **React 18 + TypeScript + Vite** | Typed API contracts, fast builds | Next.js (SSR not needed) |
| Charts | **Recharts** | Composable React charts | Chart.js (imperative) |
| Data fetch | **TanStack Query** | Caching, retries, invalidation | Redux (boilerplate) |
| Auth | **JWT (PyJWT) + bcrypt** | Stateless, horizontally scalable | Sessions (sticky) |
| CI/CD | **GitHub Actions** | Native, free for public repos | Jenkins (self-hosted tax) |
| Orchestration | **Airflow 2.9 (optional profile)** | Industry DAG scheduling | — |

### Key architectural rules

1. **Route handlers contain no business logic and no GitHub/ML calls.** They validate
   with Pydantic, call a service, and map to a response schema.
2. **The GitHub API is never called from a route.** `services/github_client.py` is the
   only module that knows about tokens, pagination, ETag caching, or rate limits.
3. **Predictions always record provenance** — `model_version`, `model_name`,
   `input_hash`, `probability` — persisted to `predictions`.
4. **Training is reproducible**: seeded, with a `manifest.json` (data hash, feature list,
   hyper-parameters, metrics, library versions) and an MLflow run.
5. **No invented metrics.** Every number in the UI is read from MLflow/DB at runtime.
6. **The target must never be a function of the features.** Two labels were rebuilt after
   violating this; both cases are documented in `ml/docs/LABELS.md`.

---

## 4. Database Schema (PostgreSQL)

19 tables across two migrations: `0001_initial` (the 16 analytical tables) and
`0002_team_access` (teams, membership and per-repository grants). Full DDL lives in
`backend/alembic/versions/`. ER diagram: [`DATABASE.md`](DATABASE.md).

A drift gate (`backend/scripts/check_schema.py`) compares the live schema to the ORM
and fails CI on any difference in **table presence, column names, column nullability,
or primary keys**. Nullability is checked deliberately: a migration that declares a
column `NOT NULL` where the model says `nullable=True` produces a database that looks
correct and then rejects every legitimate `NULL` insert, which no existence-only check
would catch.

```
users ──┬──< repositories ──┬──< commits ───< file_changes
        │                  ├──< pull_requests
        │                  ├──< issues ──┬──< issue_comments
        │                  │             └──< issue_label_map >── issue_labels
        │                  ├──< releases
        │                  ├──< contributors
        │                  ├──< predictions
        │                  ├──< analytics_snapshots
        │                  ├──< refresh_jobs
        │                  └──< repository_access
        ├──< ml_runs
        ├──< teams (as manager)
        ├──< team_members
        └──< repository_access (as direct grant)

teams ──< team_members ──> users
teams ──< repository_access
model_versions ──< predictions
```

| Table | Purpose | Key columns / constraints |
|---|---|---|
| `users` | App accounts | `email`/`username` UNIQUE, `hashed_password`, `role` |
| `repositories` | Tracked project | `(owner_name, name)` UNIQUE, stars/forks, `sync_status` |
| `contributors` | Per-repo contributor | `(repository_id, github_login)` UNIQUE, commits, churn |
| `commits` | Real commit history | `(repository_id, sha)` UNIQUE, LOC, `is_merge`, `is_bugfix` |
| `file_changes` | Per-file diffstat | `path`, `change_type`, additions/deletions |
| `pull_requests` | PRs | `(repository_id, number)` UNIQUE, `merged`, durations |
| `issues` | Issues (+PRs, flagged) | `(repository_id, number)` UNIQUE, `is_pull_request`, `category` |
| `issue_labels` | Label dictionary | `name` UNIQUE |
| `issue_label_map` | M2M join | composite PK |
| `issue_comments` | Discussion | FK → issues |
| `releases` | Tags/releases | `(repository_id, tag_name)` UNIQUE |
| `predictions` | Inference audit log | `model_version_tag`, `input_hash`, `probability_json` |
| `model_versions` | Registry mirror of MLflow | `(name, version)` UNIQUE, `data_hash`, metrics |
| `ml_runs` | Every training run | params, metrics, CV scores |
| `analytics_snapshots` | Materialised metrics | `(repo, date, granularity)` UNIQUE |
| `refresh_jobs` | Ingestion ledger | status, rows, API calls, error |
| `teams` | A group with shared repository access | `slug` UNIQUE, `manager_id` → users |
| `team_members` | Membership + team role | `(team_id, user_id)` UNIQUE, `scoped_repository_id` |
| `repository_access` | Per-repository grant | one of `user_id`/`team_id` (CHECK), `permission`, `expires_at` |

**Indexes:** FK on every relationship; `(repository_id, authored_at DESC)` for time
series; GIN on `issues` full-text; partial indexes where useful. PG ENUMs for all state
fields, stored by value.

**No mega-JSON.** `JSONB` appears only where the shape is genuinely open-ended: metrics,
hyper-parameters, per-prediction explanations.

---

## 5. API Design

Base `/api`. Auth `Authorization: Bearer <JWT>`. Uniform pagination and error envelope.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` · `/api/health/github` | Liveness, dependency status, quota |
| `POST` | `/api/auth/register` · `/login` | Issue a JWT |
| `GET` | `/api/auth/me` | Current profile |
| `GET` | `/api/teams` · `/teams/{id}` | Teams you belong to, with members and grants |
| `POST` | `/api/teams` · `PATCH /teams/{id}` | Create / update a team |
| `POST` | `/api/teams/{id}/members` | Add a member (`manager`/`member`/`viewer`, optional repo pin) |
| `PATCH`/`DELETE` | `/api/teams/{id}/members/{user_id}` | Change role or scope / remove |
| `POST` | `/api/teams/{id}/access` | Grant the team a repository (`read`/`write`/`admin`) |
| `DELETE` | `/api/teams/{id}/access/{grant_id}` | Revoke it for everyone at once |
| `POST` | `/api/teams/access` | Direct user grant (administrators only) |
| `GET` | `/api/teams/access/me` | Everything you can reach, and how |
| `POST` | `/api/repositories/analyze` | Ingest a GitHub URL (idempotent) |
| `GET` | `/api/repositories` | List (filter/sort/paginate) |
| `GET` | `/api/repositories/{id}` | Detail + health score |
| `GET` | `/api/repositories/{id}/analytics` | All computed metrics |
| `GET` | `/api/repositories/{id}/health` | Five scored indicators |
| `GET` | `/api/repositories/{id}/trends` | `?granularity=daily\|weekly\|monthly` |
| `GET` | `/api/repositories/{id}/contributors` | Leaderboard |
| `GET` | `/api/repositories/{id}/commits[/{sha}]` | Paginated commits, per-commit diffstat |
| `GET` | `/api/repositories/{id}/issues` | Filter by state/category/search |
| `GET` | `/api/repositories/{id}/pull-requests` | Filter by merged |
| `GET` | `/api/repositories/{id}/releases` | Release history |
| `GET` | `/api/jobs[/{id}]` | Ingestion job ledger |
| `POST` | `/api/ml/defect-risk` | LOW/MEDIUM/HIGH + probability |
| `POST` | `/api/ml/classify-issue` | 6 categories + confidence |
| `POST` | `/api/ml/predict-priority` | 4 levels + disclaimer |
| `POST` | `/api/ml/estimate-effort` | Hours + interval |
| `POST` | `/api/ml/classify-batch` | Bulk classification |
| `GET` | `/api/ml/models` · `/models/comparison` · `/models/reload` | Registry + comparison |
| `GET` | `/api/ml/evaluation/{name}` | Full evaluation report |
| `GET` | `/api/ml/predictions` | Prediction audit log |

Full reference with request/response bodies and error codes: [`API.md`](API.md).
Copy-pasteable requests: [`EXAMPLES.md`](EXAMPLES.md).

---

## 6. ML Strategy

Full methodology: [`ML.md`](ML.md). Label definitions and limitations:
`ml/docs/LABELS.md`.

### Dataset (all real)

| Source | Content | How obtained |
|---|---|---|
| `pallets/click`, `psf/requests`, `pallets/flask`, `encode/httpx`, `expressjs/express` | 23,784 real commits, 41,358 real file diffstats, real authors and timestamps | `git clone` — the git transport is **not** rate-limited like the REST API |
| Public GitHub issue corpus | 3,569 real issues as verbatim REST API objects | public dataset mirror |
| Live GitHub REST API | repo metadata, live sync | `GitHubClient` with ETag cache + token |

### MODEL 1 — Defect Risk (binary → LOW/MEDIUM/HIGH)
- **Unit:** one real commit. **Target:** the bug-inducing-change proxy — another author
  fixes the same paths within 30 days with a fix-intent commit.
- **Features:** 49 — change size (log-scaled), message signal, timing, file composition
  and class, author history, repository activity windows, per-file history. All
  leakage-guarded.
- **Candidates:** Logistic Regression, Random Forest, Gradient Boosting, XGBoost. Selected
  on stratified 5-fold CV macro-F1, then evaluated on a chronological holdout.
- **Bands:** `LOW < 0.33 ≤ MEDIUM < 0.66 ≤ HIGH`.

### MODEL 2 — Issue Classification (6 classes)
TF-IDF (word 1–2-gram ⊕ char\_wb 3–5-gram via `FeatureUnion`) → Logistic Regression and
calibrated Linear SVM. Labels derived from real label vocabulary; rows with no signal are
dropped and the drop count is recorded.

### MODEL 3 — Issue Priority (4 classes)
Hybrid: TF-IDF(title) ⊕ structured features. The target is **label-only** — building it
from text while also supplying text as a feature leaks and produced a meaningless 0.95
F1, so it was rebuilt. Label-presence features are excluded from this model for the same
reason. The response always carries a `disclaimer`.

### MODEL 4 — Development Effort (regression)
Regresses `log1p(resolution_hours)`; reports MAE/RMSE/R² on the original hour scale, plus
within-±20 % and within-2× hit rates, with an interval from the measured residual IQR.
The time-to-close proxy is stated in the response and the UI.

### Cross-cutting
- **Split:** chronological 85/15 for the defect model (realistic deployment), stratified
  85/15 for the issue models. Seeded.
- **Leakage guards:** expanding windows over prior events only, asserted by a regression
  test; target-never-a-feature rule, asserted by label tests.
- **Evaluation:** `ml/evaluation/metrics.py` computes everything from real predictions;
  `ml/reports/*.md` renders the report.
- **Tracking:** MLflow when enabled, otherwise skipped — an unreachable server must never
  stall a training run.

### Artefact layout
```
ml/artifacts/defect_risk/v1/
├── model.joblib        # fitted estimator
├── manifest.json       # data_hash, features, hyper-params, metrics, CV, limits
├── comparison.json     # every candidate's real metrics
└── report.json
```

---

## 7. Folder Structure

```
devinsight/
├── backend/
│   ├── app/
│   │   ├── main.py                 # app factory, middleware, error handlers
│   │   ├── core/{config,security,logging,errors}.py
│   │   ├── db/{base,session}.py
│   │   ├── models/                 # 19 SQLAlchemy models
│   │   ├── schemas/                # Pydantic v2 request/response
│   │   ├── api/v1/                 # auth, repositories, ml, teams, health
│   │   ├── services/               # access, github_client, ingestion, analytics, ml_service
│   │   └── ml/                     # (runtime loader lives in services/ml_service)
│   ├── alembic/versions/{0001_initial,0002_team_access}.py
│   ├── scripts/check_schema.py     # drift gate: tables, columns, nullability, PKs
│   ├── tests/                      # unit, API, analytics, ML, access
│   ├── pyproject.toml
│   └── requirements.txt
├── ml/
│   ├── config.py                   # paths, seeds, targets, limitations
│   ├── data/{acquire,github_history,issue_corpus}.py
│   ├── preprocessing/{clean_history,clean_text,build_datasets}.py
│   ├── features/{defect_features,issue_features}.py
│   ├── models/{candidates,hybrid}.py
│   ├── training/{common,run_all,train_*}.py
│   ├── evaluation/metrics.py
│   ├── inference/{registry,predictor}.py
│   ├── artifacts/                  # trained models (git-ignored)
│   ├── reports/                    # generated evaluation reports
│   └── docs/{METHODOLOGY,LABELS}.md
├── frontend/src/{api,components,pages,context,lib,types,__tests__}
├── airflow/dags/devinsight_etl.py
├── data/{raw,processed,features}/
├── mlflow/                         # tracking store mount
├── docker/{backend,frontend,mlflow}.Dockerfile · nginx.conf
├── docs/{ARCHITECTURE,DATABASE,API,ML,DEPLOYMENT,EXAMPLES}.md
├── .github/workflows/{ci,deploy}.yml
├── scripts/{smoke_test,model_summary,seed_teams,verify_access,add_repo,check_*}.py
├── tests/test_artifacts.py         # trained-model quality gate
├── docker-compose.yml · .env.example · Makefile · README.md · LICENSE
```

## 8. Quality Gates

| Gate | Tool | Blocking? |
|---|---|---|
| Lint | `ruff check` | yes |
| Format | `ruff format --check` | yes |
| Types | `tsc --noEmit` (frontend), `mypy` (backend, advisory) | TS yes |
| Schema drift | `scripts/check_schema.py` | yes |
| Backend + ML tests | `pytest` | yes |
| Frontend tests | `vitest` | yes |
| Frontend build | `vite build` | yes |
| Model quality | `tests/test_artifacts.py` | yes |
| Secrets | CI pattern scan for tokens / `.env` | yes |
| Deploy | `docker compose up` + health probe | tag-gated |

## 9. Security Model

- Passwords: **bcrypt** cost 12, pre-truncated to bcrypt's 72-byte limit, never logged.
- JWT: HS256, claims `sub`/`role`/`jti`/`iat`/`nbf`/`exp`/`iss`, verified every request.
- Role gate: `viewer` < `analyst` < `admin`.
- **GitHub tokens are read only from server env vars.** Never returned by any endpoint,
  never logged (the logger redacts `token`/`authorization`/`password`/`secret_key`), and
  never bundled into the frontend. The health endpoint exposes only a hash *fingerprint*.
- SQL injection: exclusively SQLAlchemy parameter binding.
- SSRF guard: `parse_repo_url` accepts only `github.com` hosts.
- CORS allow-list from `CORS_ORIGINS`, credentials disabled.

### Authorization

Authentication answers *who*; authorization answers *what they may touch*. A valid JWT
is not enough to read a repository.

Two independent inputs decide access, and both must agree:

- the **global role** on the user (`admin` / `analyst` / `viewer`) sets a **ceiling**;
- a **repository grant** — addressed to the user directly, or to a team they belong
  to — decides **which** repositories exist for them at all.

```
effective(user, repo) = min(role_ceiling, max(all applicable grants))
                        read < write < admin
```

All of it lives in `backend/app/services/access.py`, and every repository route
resolves through it. The rules that are easy to get wrong, each with a regression test
in `backend/tests/test_access.py`:

- **Managing a team never widens what it can reach.** A project manager administers the
  repositories their team was granted and nothing more. A manager with no grant on a
  repository cannot see it — this was a real defect during the build, where the manager
  branch granted `admin` on every repository in the platform.
- **A team role cannot exceed the team's own grant.** A `manager` of a read-only team
  reads. The grant is the ceiling; team-level authority (`manage_access`) is separate.
- **A scoped membership is a hard boundary.** Pinning a member to repository 7 hides
  repository 8 even when their team holds both.
- **Revoking a team grant revokes it for everyone at once.** Access is revoked by
  default rather than granted by exception, which is the entire reason teams exist.
- **Administrators bypass the grant table**, the one deliberate exception, so the
  platform stays recoverable.

`GET /api/teams/{id}` returns a `capabilities` list and the UI hides the controls the
caller lacks. That is presentation only. Each route re-checks server-side, so a
hand-crafted request gains nothing.

## 10. Phase Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Architecture, tech decisions, DB schema, API design, ML strategy | ✅ this document |
| 2 | PostgreSQL + SQLAlchemy + Alembic | ✅ 19 tables, drift-gated |
| 3 | FastAPI foundation, config, errors, health | ✅ |
| 4 | GitHub service layer | ✅ ETag cache, rate limit, retry, pagination |
| 5 | ETL ingestion + Airflow DAG | ✅ idempotent, job ledger |
| 6 | Cleaning + EDA | ✅ |
| 7 | Feature engineering | ✅ 49 leakage-guarded features |
| 8 | Defect-risk model | ✅ trained + evaluated |
| 9 | Issue classifier | ✅ trained + evaluated |
| 10 | Priority model | ✅ trained + evaluated (label rebuilt for leakage) |
| 11 | Effort model | ✅ trained + evaluated |
| 12 | MLflow tracking | ✅ opt-in |
| 13 | React dashboard | ✅ 8 views |
| 14 | Frontend ↔ FastAPI | ✅ typed client, end-to-end verified |
| 15 | Authentication | ✅ JWT + roles |
| 16 | Dockerisation | ✅ compose, 4 services |
| 17 | Automated testing | ✅ 196 backend + 36 frontend |
| 18 | GitHub Actions CI/CD | ✅ ci + deploy |
| 19 | Deployment | ✅ runbook + workflow |
| 20 | Documentation | ✅ |
| 21 | Teams & code-access control | ✅ project managers, per-repo grants |
