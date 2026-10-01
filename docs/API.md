# API Reference

Base URL: `http://localhost:8000/api`
Interactive docs: `http://localhost:8000/docs` (Swagger) · `http://localhost:8000/redoc`
Machine-readable schema: `http://localhost:8000/openapi.json`

---

## Conventions

**Authentication.** Every endpoint except `/api/health` and the two auth entry points
requires a JWT:

```
Authorization: Bearer <access_token>
```

Obtain one with `POST /api/auth/login`. Tokens carry `sub`, `role`, `jti`, `iat`, `nbf`,
`exp` and `iss`, and are verified on every protected route. Roles are hierarchical:
`viewer` < `analyst` < `admin`. Write operations require at least `analyst`.

**Pagination.** All list endpoints share one envelope:

```json
{ "items": [ ... ], "total": 1234, "page": 1, "page_size": 50, "pages": 25 }
```

`page` is 1-based; `page_size` is capped (usually 200).

**Errors.** Every failure returns the same shape, plus a correlation id:

```json
{
  "detail": "repository 42 does not exist",
  "code": "repository_not_found",
  "request_id": "9f2c1a4b7e01",
  "context": { "...": "optional" }
}
```

| Code | Status | Meaning |
|---|---|---|
| `authentication_error` / `invalid_token` | 401 | Missing, invalid or expired JWT |
| `permission_denied` / `insufficient_role` | 403 | Role below the required level |
| `not_found` / `repository_not_found` | 404 | Resource does not exist |
| `request_validation_error` | 422 | Pydantic validation failed; `context.errors` names each field |
| `github_not_found` | 404 | Upstream GitHub resource missing |
| `rate_limit_exceeded` | 429 | GitHub API quota exhausted |
| `model_not_available` | 503 | No trained model for that endpoint |
| `github_error` | 502 | Upstream GitHub failure after retries |
| `internal_error` / `database_error` | 500 | Unexpected failure |

**Response headers.** `X-Request-ID` (echo of your `X-Request-ID` or generated) and
`X-Process-Time` (server-side duration) are attached to every response.

**Rate limiting.** The platform does not throttle its own clients; the `429` you may see
comes from GitHub. Configure `GITHUB_TOKEN` to raise the upstream quota from 60 to
5,000 requests/hour.

---

## System

### `GET /api/health`
Liveness plus dependency status. Public.

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "environment": "development",
  "database": "connected",
  "models_loaded": ["defect_risk-v1", "issue_classifier-v1"],
  "timestamp": "2025-06-01T10:00:00+00:00"
}
```

### `GET /api/health/github`
GitHub reachability and remaining quota. Returns a token *fingerprint*, never the token.

---

## Authentication

### `POST /api/auth/register` → `201`
```json
{ "email": "you@example.com", "username": "yourname", "password": "Passw0rd123", "full_name": "Your Name" }
```
Password must be ≥ 8 characters with at least one letter and one digit. Passwords are
hashed with bcrypt (cost 12) — plaintext is never stored. The **first** account created
becomes `admin`; later accounts become `analyst`.

### `POST /api/auth/login`
```json
{ "username": "yourname", "password": "Passw0rd123" }
```
Accepts a username or an email. Returns `access_token`, `expires_at` and the `user`
object. An incorrect username and an incorrect password return the identical 401 message,
so the endpoint does not reveal which accounts exist.

### `GET /api/auth/me`
The authenticated user.

---

## Teams and code access

Access to a repository is not implied by having an account. Two independent inputs
decide what you can do, and both must agree:

1. your **global role** — `admin` / `analyst` / `viewer` — which sets a *ceiling*;
2. a **repository grant**, addressed to you directly or inherited from a team, which
   decides *which* repositories are visible at all.

So an `analyst` in a team holding `admin` on a repository gets `write`, and a project
manager who is not a member of a team cannot see that team at all. See
[Authorization](#authorization) for the full rule set.

### `GET /api/teams`
Filters: `mine` (only teams you manage); `active_only` (default `true`).
A non-administrator only sees teams they belong to or manage.

```json
{
  "items": [{
    "id": 1,
    "name": "Platform Engineering",
    "slug": "platform-engineering",
    "description": "Owns the shared CLI tooling and runtime libraries.",
    "manager_id": 3,
    "manager_username": "priya",
    "is_active": true,
    "member_count": 3,
    "repository_count": 2,
    "my_role": "manager",
    "capabilities": ["view", "manage_access", "manage_members"],
    "created_at": "2026-09-30T12:00:00Z"
  }],
  "total": 2, "page": 1, "page_size": 25, "pages": 1
}
```

`capabilities` tells the UI which controls to render. It is presentation only — every
rule below is enforced server-side regardless.

### `POST /api/teams` → `201`
Body: `{name, description?, manager_username?}`. You become the project manager.

### `GET /api/teams/{id}`
Adds `members[]` and `repositories[]` to the team summary. `403` if you are not a
member (`not_a_team_member`).

### `PATCH /api/teams/{id}`
Rename, re-describe, or deactivate. Managers only.

### `POST /api/teams/{id}/members` → `201`
Body: `{username, role?, scoped_repository_id?}`.

`role` is `manager` | `member` | `viewer`. `scoped_repository_id` optionally pins the
member to a single repository *inside* the team, which is how you give someone review
access to one service without widening it to everything the team can reach.

Errors: `409 already_a_member`, `404 user_not_found`, `403 not_team_manager`.

### `PATCH /api/teams/{id}/members/{user_id}`
Change a member's `role`, or set/clear their `scoped_repository_id`.

### `DELETE /api/teams/{id}/members/{user_id}` → `204`
Removes the membership. The repository grants themselves are untouched — they belong
to the team, not the person.

### `POST /api/teams/{id}/access` → `201`
Body: `{repository_id, permission, expires_at?, note?}`.
`permission` is `read` | `write` | `admin`. Granting the same repository twice updates
the existing row rather than creating a duplicate. Managers only.

### `DELETE /api/teams/{id}/access/{grant_id}` → `204`
Revokes the team's access. Every member immediately loses it.

### `POST /api/teams/access` → `201`
Body: `{repository_id, user_id, permission, note?}`. Grants one user access to one
repository without going via a team. **Administrators only.**

### `GET /api/teams/access/me`
Everything the caller can currently reach, and how they got it. This is the endpoint
that makes the model explainable.

```json
{
  "user_id": 3,
  "username": "priya",
  "role": "analyst",
  "manages_teams": 1,
  "repositories": [
    { "repository_id": 3, "full_name": "pallets/click", "permission": "write",
      "source": "team", "via_teams": ["Platform Engineering"] },
    { "repository_id": 4, "full_name": "pallets/flask", "permission": "read",
      "source": "team", "via_teams": ["Platform Engineering"] }
  ]
}
```

`source` is `admin` (bypasses grants), `direct` (a grant addressed to you), or `team`
(inherited, with `via_teams` naming the teams). `permission` is the *effective*
permission after the global-role ceiling is applied — a `viewer` in an `admin` team is
reported as `read`, so the UI never promises more than the server will allow.

## Repositories

### `POST /api/repositories/analyze` → `202`
Register a repository and start ingestion. Requires `analyst`.

```json
{
  "url": "https://github.com/pallets/click",
  "ingest_history": true,
  "max_commits": 3000,
  "max_issues": 1000,
  "background": true
}
```

`url` accepts a full GitHub URL, a clone URL, or plain `owner/name`. **Only
`github.com` hosts are permitted** — this is an SSRF guard, not a convenience.

```json
{
  "repository_id": 1,
  "full_name": "pallets/click",
  "job_id": null,
  "status": "pending",
  "message": "repository registered; ingestion running in the background"
}
```

The call is **idempotent**: a repository ingested within the last hour returns its cached
result instead of re-hitting GitHub. Set `"background": false` for a synchronous run
(returns the `job_id` and the row count).

### `GET /api/repositories`
Filters: `search`, `language`, `sync_status`; sort: `created_at|stars|name|last_synced_at`.
Returns a `Page[Repository]`.

**Scoped to your access.** Non-administrators only receive repositories they have been
granted, directly or through a team, and `total` counts only those — the count cannot
be used to infer how many hidden repositories exist.

### `GET /api/repositories/{id}` and every `/api/repositories/{id}/...` route
All repository sub-resources are gated. A caller without a grant gets `403`
`no_repository_access` rather than a `404`, so a legitimate user can tell "no access"
apart from "does not exist".

### `GET /api/repositories/{id}`
Repository detail plus a composite `health_score`.

### `GET /api/repositories/{id}/analytics`
Every metric in the analytics spec, plus `label_distribution` and
`category_distribution`.

```json
{
  "total_commits": 3380,
  "total_contributors": 214,
  "open_issues": 12,
  "closed_issues": 431,
  "total_pull_requests": 903,
  "merged_pull_requests": 866,
  "average_pr_size": 41.7,
  "average_issue_resolution_hours": 812.4,
  "median_issue_resolution_hours": 190.2,
  "commit_frequency_per_week": 8.4,
  "release_frequency_per_month": 1.6,
  "issue_closure_rate": 97.3,
  "pr_merge_rate": 95.9,
  "average_merge_duration_hours": 26.4,
  "total_churn": 214883,
  "bugfix_ratio": 18.7,
  "days_since_last_commit": 3,
  "top_contributors": [ ... ],
  "most_changed_files": [ ... ],
  "label_distribution": [ ... ],
  "category_distribution": [ ... ]
}
```

### `GET /api/repositories/{id}/health`
Five scored indicators (development activity, issue backlog, code-review flow, bus factor,
defect-fix pressure), each with `score`, `status` and a human-readable `detail`, plus the
defect-risk distribution across all predictions served for this repository.

### `GET /api/repositories/{id}/trends`
Query: `granularity=daily|weekly|monthly`, `metric=commits|issues|prs|bug_fixes|releases|contributors`,
`limit`.

```json
{
  "repository_id": 1,
  "granularity": "weekly",
  "metric": "commits",
  "points": [ { "period": "2025-05-05", "commits": 9, "issues_opened": 2, "prs_merged": 4, ... } ],
  "totals": { "commits": 3380, "issues_opened": 443, ... }
}
```

### `GET /api/repositories/{id}/contributors`
Contributor leaderboard, ordered by commits. `Page[Contributor]`.

### `GET /api/repositories/{id}/commits`
Filters: `author`, `is_bugfix`. `Page[Commit]`.
`GET /api/repositories/{id}/commits/{sha}` returns the commit with its full per-file diffstat.

### `GET /api/repositories/{id}/issues`
Filters: `state`, `category`, `search`, `include_pull_requests`. `Page[Issue]`.

### `GET /api/repositories/{id}/pull-requests`
Filter: `merged`. `Page[PullRequest]`.

### `GET /api/repositories/{id}/releases`
Release/tag history, newest first.

### `GET /api/jobs` · `GET /api/jobs/{id}`
The ingestion job ledger: status, rows ingested, API calls made, duration, error.

---

## Machine learning

### `POST /api/ml/defect-risk`
```json
{
  "repository": "pallets/click",
  "author": "Rowlando13",
  "commit_message": "Refactor parameter parsing",
  "changes": [
    { "path": "src/click/core.py", "change_type": "modified", "additions": 180, "deletions": 40 },
    { "path": "tests/test_core.py",  "change_type": "added",    "additions": 60,  "deletions": 5 }
  ]
}
```

```json
{
  "risk_level": "HIGH",
  "probability": 0.8231,
  "message": "HIGH defect risk (82%). Recommend extra review, a smaller change set, and explicit test coverage before merge.",
  "contributing_factors": [
    { "feature": "log_churn", "value": 4.79, "unit": "log lines changed", "model_importance": 0.118 }
  ],
  "model_name": "defect_risk",
  "model_version": "defect_risk-v1",
  "prediction_id": 4711,
  "computed_at": "2025-06-01T10:05:00+00:00"
}
```

Bands: `LOW < 0.33 ≤ MEDIUM < 0.66 ≤ HIGH`.

### `POST /api/ml/classify-issue`
```json
{ "title": "AttributeError: _Token has no attribute _name", "body": "…", "labels": ["bug", "regression"] }
```
```json
{
  "category": "BUG",
  "confidence": 0.9412,
  "probabilities": { "BUG": 0.9412, "FEATURE_REQUEST": 0.0301, "QUESTION": 0.0180, "...": 0.0 },
  "top_features": ["traceback (0.4210)", "attributeerror (0.3187)"],
  "model_version": "issue_classifier-v1"
}
```

### `POST /api/ml/predict-priority`
```json
{ "title": "Production database is corrupted", "body": "…", "labels": ["bug"], "comments_count": 6, "author_association": "MEMBER" }
```
```json
{
  "priority": "CRITICAL",
  "confidence": 0.8834,
  "probabilities": { "CRITICAL": 0.8834, "HIGH": 0.0902, "MEDIUM": 0.0201, "LOW": 0.0063 },
  "contributing_factors": ["has_critical_language", "has_stacktrace"],
  "disclaimer": "This priority is a machine-learning estimate derived from patterns in historical issues. It is an advisory signal only and must not be treated as an authoritative priority decision. Human triage remains the source of truth.",
  "model_version": "issue_priority-v1"
}
```

### `POST /api/ml/estimate-effort`
```json
{ "title": "Implement OAuth2 device-code login flow", "body": "…", "labels": ["feature"] }
```
```json
{
  "estimated_hours": 6.4,
  "estimate_low_hours": 1.9,
  "estimate_high_hours": 10.9,
  "unit": "hours",
  "confidence": 0.62,
  "contributors": { "mean_absolute_error_hours": 143.2, "within_20pct_rate": 38.1, "r2": 0.114 },
  "methodology_note": "Trained on time-to-close (created_at → closed_at) of historical GitHub issues, because GitHub does not record engineering hours. Time-to-close includes queue and wait time, so this is a coarse proxy for hands-on effort, not a timesheet.",
  "model_version": "issue_effort-v1"
}
```

### `POST /api/ml/classify-batch`
```json
{ "repository_id": 1, "persist": true, "issues": [ { "title": "#123 failing test", "body": "…" } ] }
```
Up to 500 issues. With `persist: true`, categories are written back to the `issues` rows.

### `GET /api/ml/models`
Every trained model version with its measured metrics.

```json
{
  "items": [ {
    "name": "defect_risk", "version": 1, "tag": "defect_risk-v1",
    "algorithm": "xgboost", "dataset_version": "v1.0.0", "data_hash": "a91c…",
    "n_train": 20216, "n_test": 3568, "features": ["log_churn", "…"],
    "metrics": { "accuracy": 0.8401, "precision": 0.8103, "recall": 0.7987,
                 "f1": 0.8044, "f1_macro": 0.8471, "roc_auc": 0.9128 },
    "is_active": true, "trained_at": "2025-06-01T09:41:00+00:00",
    "target_description": "…", "limitations": "…"
  } ],
  "loaded": ["defect_risk-v1"],
  "total": 1
}
```

### `GET /api/ml/models/comparison`
One `ModelComparison` per target, with the **actual measured** numbers for every candidate.

```json
[ {
  "name": "defect_risk",
  "selection_criterion": "5-fold stratified cross-validation on f1_macro (higher is better)",
  "selected_model": "xgboost",
  "rows": [
    { "model": "xgboost", "accuracy": 0.8401, "precision": 0.8103, "recall": 0.7987,
      "f1": 0.8044, "roc_auc": 0.9128, "cv_f1_mean": 0.8446,
      "training_time_seconds": 8.84, "selected": true },
    { "model": "random_forest", "...": "...", "selected": false }
  ]
} ]
```

### `GET /api/ml/evaluation/{name}`
The full report: metrics, per-class breakdown, confusion matrix and labels, CV folds,
feature importance, the candidate comparison, hyperparameters, dataset provenance, the
target definition and the stated limitations.

### `GET /api/ml/predictions`
Audit log. Filters: `target_type`, `repository_id`. Every row carries the model version
that produced it, the prediction, the probability, the input hash and the latency.

---

## Security notes

- **GitHub tokens are server-side only.** They are read from the process environment by
  `services/github_client.py`, never returned by any endpoint, and never bundled into the
  frontend build. The logger redacts `token`, `authorization`, `password`, `secret_key`
  and friends.
- **No SQL injection surface.** All database access goes through SQLAlchemy parameter
  binding; no string-built SQL.
- **SSRF guard.** `parse_repo_url` only accepts `github.com` hosts.
- **Passwords** are bcrypt-hashed and truncated safely at bcrypt's 72-byte limit.
- **CORS** is an allow-list from `CORS_ORIGINS` with credentials disabled (the API is
  token-authenticated, not cookie-based).

## Authorization

Authorization is resolved in `backend/app/services/access.py`, and every repository
route goes through it. There is no path that reads a repository without asking.

### The rule

```
effective(user, repo) = min( role_ceiling(user.role),  max( all applicable grants ) )

role_ceiling:  admin -> admin   analyst -> write   viewer -> read
grants:        every unexpired repository_access row for this repo where either
                 - user_id  = the user          (a direct grant), or
                 - team_id   = a team the user belongs to, subject to that
                               membership's scoped_repository_id
```

`min` and `max` are the ordering `read < write < admin`. If no grant applies, the
result is `None` and the repository is invisible.

### Consequences worth knowing

These are the rules that are easy to get wrong, and each has a regression test in
`backend/tests/test_access.py`:

- **A project manager administers their team's repositories, and nothing else.** Being a
  manager does *not* grant access to repositories the team was never granted. A
  manager with no grant on a repository cannot see it.
- **A team role cannot exceed the team's own grant.** A `manager` of a team holding
  only `read` reads; they do not administer. The grant is the ceiling, not the role.
  Team-level authority still works: they can change *who* has access without gaining
  more access themselves.
- **A scoped membership is a hard boundary.** A member pinned to repository 7 cannot
  reach repository 8 even when their team holds both.
- **Administrators bypass the grant table** so the platform is always recoverable.
  This is the one deliberate exception to the rule above.
- **Revoking a team grant revokes it for everyone** in that team at once, which is the
  reason teams exist: access is revoked by default, not granted by exception.

### Why the frontend cannot be the boundary

`GET /api/teams/{id}` reports `capabilities` and the UI hides the controls you do not
have. That is a usability feature. Every one of those routes independently re-checks
the caller's ability server-side and returns `403` regardless of what the client sent,
so a hand-crafted request gains nothing. The `TestTeamsApi` and
`TestRepositoryRoutesAreGated` test classes assert exactly that.
