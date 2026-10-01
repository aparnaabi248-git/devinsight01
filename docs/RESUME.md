# Resume Description & Project Talking Points

## 1. Résumé entry (compact)

**DevInsight — AI-Powered Software Engineering Analytics & Predictive Intelligence
Platform** · *Full-stack · Machine Learning · Data Engineering*
`FastAPI` `PostgreSQL` `React` `TypeScript` `scikit-learn` `XGBoost` `MLflow` `Airflow` `Docker` `GitHub Actions`

> Built an end-to-end platform that ingests real GitHub repository history
> (**23,784 commits / 41,358 file diffs** across 5 OSS projects), engineers **49
> leakage-guarded features**, trains and evaluates **four machine-learning models**
> (defect risk, issue classification, issue priority, effort estimation) against
> cross-validated baselines, and serves them through a **16-table PostgreSQL** backend and
> a **7-view React analytics dashboard**, tracked with MLflow and deployed via Docker
> Compose and GitHub Actions.

## 2. Longer paragraph

Designed and built a production-oriented software engineering intelligence platform. The
architectural constraint that shaped everything: **none of the required labels exist in the
data** — GitHub does not record whether a change was defective, how urgent an issue truly
is, or how many hours it took. I derived defensible proxy targets from observable history
(most notably a bug-inducing-change label based on fix-intent follow-up commits by other
authors), built explicit leakage guards for every temporal feature, **caught and rebuilt
two labels that leaked into their own features** (a priority score of 0.95 collapsed to
0.59 once the target was made label-only), and documented the limits of every proxy. All
model selection is driven by stratified cross-validation across multiple candidate
algorithms, and a CI gate fails the build if a metric is missing, undocumented, or
implausible.

## 3. Star bullet points (résumé-ready)

- Designed a **16-table normalised PostgreSQL schema** with composite natural keys that
  make ETL ingestion idempotent (`ON CONFLICT` upserts), FK/GIN/composite indexes, and a
  **schema-drift gate** that diffs the live database against the SQLAlchemy metadata in CI.
- Built a **leakage-safe feature pipeline** in which every historical feature is computed
  in a single forward pass over time-sorted commits using only prior events — enforced by a
  regression test that recomputes the matrix with future commits appended and asserts the
  earlier rows are unchanged.
- **Diagnosed and fixed a timestamp-resolution bug** where pandas' `datetime64[us]` dtype
  made a `/1e9` epoch conversion produce kiloseconds, silently corrupting every
  time-ordered lookup; caught by a purpose-built leakage test.
- Implemented a **GitHub service layer** with ETag/`Last-Modified` response caching,
  token-aware rate-limit tracking, exponential backoff, `Link`-header pagination and
  SSRF-safe URL parsing — the only module permitted to touch credentials.
- Trained and compared **11 candidate models across 4 tasks**, selecting on cross-validated
  macro-F1 (or MAE for regression) and reporting held-out metrics, confusion matrices,
  per-class breakdowns and feature importance.
- Shipped **196 backend/ML tests and 36 frontend tests**, including a
  **trained-model quality gate** that rejects undocumented models, single-candidate
  "selections", and metrics outside plausible ranges — the check that catches a hard-coded
  placeholder masquerading as a measurement.
- Built a **teams and code-access layer** where a project manager owns a team and grants
  it repositories: two independent inputs (a global role ceiling and per-repository
  grants, direct or inherited) decide access, every repository route resolves through
  one service, and each rule has a matching denial test.
- Containerised the stack in **4 services** (multi-stage builds, non-root, health checks)
  with **CI/CD** covering lint, types, schema drift, tests, image build and a
  health-gated deploy.

## 4. The two most interesting engineering decisions

These are the parts worth raising in an interview, because they show judgement rather
than just execution.

### 4.1 "The target must never be a function of the features"

The priority model's first version derived its target from the issue's *text* (data-loss
phrasing → CRITICAL) and *comment count* — and then supplied that same text and comment
count to the model as *features*. Cross-validated F1 came out at **0.95**, which looked
excellent and meant nothing: the model was reproducing my own regexes.

I rebuilt the target to be **label-only** and removed the label-presence features
(`has_feature_label` and friends), which were effectively the answer key. F1 dropped to
**0.59** — and that number is real. It measures how well issue content predicts a triage
decision a human already made, which is the task worth building. The regression is now
locked in by a test asserting `label_priority` ignores text entirely.

The generalisable lesson: *a high score is a hypothesis about your pipeline, not a
result.* When a metric looks too good, the first thing to audit is whether the target
could be computed from the inputs.

### 4.2 Data source choice driven by the rate limit

The REST API allows 60 requests/hour unauthenticated, and `pallets/click` alone has 3,377
commits. Bulk ingestion over REST was not viable. Cloning over the **git transport** — which
is not subject to that quota — yields the *complete* per-file diffstat (real line
additions/deletions, authorship, timing) that the REST API only approximates, so the
git-based source is both feasible and higher-fidelity. A second public corpus supplied the
issue text and labels that git history cannot contain.

### 4.3 An authorization bug that only a negative test could have caught

The access layer resolves permission as `min(global role ceiling, strongest applicable
grant)`. During the build the team-manager branch read as *"the team has no grant for this
repository, so grant `admin` anyway"* — which handed **every project manager visibility of
every repository on the platform**. It passed every test written from the happy path.

The fix was to make the grant mandatory and the ceiling explicit, and then to rewrite the
tests so that **every permission has a matching denial**: a manager cannot see a
repository their team lacks, a `viewer` cannot exceed `read` even in an `admin` team, and
revoking a team grant removes it for all members at once. `verify_access.py` proves the
same properties against the running server.

The transferable point: *authorisation is the one area where asserting only the allowed
case is close to worthless*, because the failure mode is a silent over-grant that looks
identical to correct behaviour in a demo.

## 5. Numbers you can quote

| Metric | Value |
|---|---|
| Real commits ingested | 23,784 across 5 repositories |
| Real per-file diffs | 41,358 |
| Real issues in the text corpus | 3,569 |
| Engineered features (defect model) | 49 |
| Candidate models compared | 11 (4 + 2 + 2 + 3) |
| Database tables | 19 |
| Backend + ML tests | 196 |
| Frontend tests | 36 |
| Defect-label positive rate | 23.7 % |
| Defect model ROC-AUC (held-out) | 0.85 |
| Docker services | 4 |

*(The metric values above come from the run recorded in `ml/reports/`; regenerate with
`python scripts/model_summary.py`.)*

## 6. Honest limitations to state up front

Claiming these unprompted reads as engineering maturity rather than weakness.

- **The defect label is a proxy.** Version control records what changed, never what broke.
  Silent defects are counted as negatives, so apparent precision is optimistic.
- **The effort target is time-to-close, not engineering hours** — it includes queue and
  wait time, so it is an upper bound on effort rather than a measurement of it. Its low R²
  is expected: most of the variance in time-to-close is scheduling, not difficulty.
- **The issue models are trained on a single project.** The reported scores measure
  within-project predictability and should not be presented as general triage accuracy.
- **Transferability is untested.** Training on five popular Python/JS OSS projects says
  nothing about performance on a closed-source codebase.

## 7. Questions to expect, and the answers

**"How do you know your model isn't just memorising?"**
The test split is chronological for the defect model (train on the past, test on the
future) and never touched during selection — selection happens on cross-validation inside
the training split only. Every candidate is refit and scored once on the holdout. The
data hash and library versions are recorded so a run is reproducible.

**"How do you prevent data leakage in temporal features?"**
Structurally, not by convention. Historical features are computed in a single forward pass
over time-sorted commits, reading only events strictly before the commit being featurised.
The invariant is asserted by a test that appends future commits and checks earlier rows are
bit-identical.

**"Why Random Forest over XGBoost for defect risk?"**
Because cross-validation said so. XGBoost had higher precision (0.65 vs 0.58) but much
lower recall (0.34 vs 0.49) and a lower positive-class F1; Random Forest won on the
selection metric. The comparison table is in `ml/reports/`.

**"How do you decide who can see which repository?"**
Two independent inputs, both required. The global role (`admin`/`analyst`/`viewer`) is a
*ceiling*; a repository grant — addressed to the person directly or to a team they belong
to — decides *which* repositories exist for them at all. The effective permission is
`min(ceiling, strongest applicable grant)`. Managing a team never widens what it can
reach, a team role can't exceed the team's own grant, and a member can be pinned to a
single repository. Revoking a team grant revokes it for everyone at once, so access is
revoked by default rather than granted by exception. See §4.3 for the bug this design
caught.

**"What would you do differently with more time?"**
The three highest-value ML changes are listed in the README's *Future improvements*:
time-to-first-response as a real effort-adjacent target instead of a queue-time proxy, a
cross-repository transfer study to put a number on the transferability claim, and quantile
regression for calibrated effort intervals. Beyond that, moving ingestion to a proper task
queue (Celery/arq) so a slow GitHub fetch cannot occupy a web worker, and an access audit
log so every membership and grant change is traceable.

**"How would you deploy this?"**
`docker compose up` for a single node; the images build cleanly in CI and push to GHCR on a
tag. The API is stateless, so it scales horizontally behind a load balancer. The database is
the scaling constraint — read replicas and pre-materialised `analytics_snapshots` for
dashboards over millions of commits. Full runbook in `docs/DEPLOYMENT.md`.
