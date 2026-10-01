# Machine Learning Methodology

Everything below is measured on real data. No metric in this project is hard-coded,
estimated, or carried over from a paper. Re-running
`python ml/training/run_all.py` regenerates every number.

---

## 1. Datasets

| Source | What it is | How it was obtained |
|---|---|---|
| `pallets/click`, `psf/requests`, `pallets/flask`, `encode/httpx`, `expressjs/express` | ~23,000 real commits with real per-file line additions/deletions, authors, timestamps and tags | `git clone` (git transport) — **not** the REST API, so no rate limit applies |
| Public GitHub issue corpus (`huggingface/datasets` repository issues) | ~3,500 real issues as verbatim GitHub REST API objects: title, body, labels, state, comments, `created_at`, `closed_at` | public dataset mirror |

Rationale for two sources rather than one: the git transport yields the **complete**
diffstat history of a repository (per-file LOC, authorship, timing) which the REST API
only approximates, while the issue corpus carries the **text and labels** the git
history cannot. Using the REST API for bulk history would also be impractical: the
unauthenticated quota is 60 requests/hour, and `pallets/click` alone has 3,377 commits.

### Reproducing the dataset

```bash
python ml/data/acquire.py            # clones 5 repos + downloads the issue corpus
cd ml && python training/run_all.py   # extracts, cleans, engineers features, trains
```

`ml/data/acquire.py` is idempotent — re-running skips what is already present.

### ETL stages

```
git clone / dataset download          EXTRACT
  → git log --numstat / --raw         per-file real diffstat
  → JSONL issue objects               verbatim
        │
        ▼
data/processed/{commits,file_changes}.csv          raw store, on disk, re-usable
        │
        ▼  ml/preprocessing/clean_history.py
clean_commits()                       drop unusable rows, derive fix-intent, hour,
                                     weekday, log-scaled churn
build_defect_labels()                 derive the defect target
        │
        ▼  ml/preprocessing/clean_text.py + ml/features/issue_features.py
clean_text() / label_category() / label_priority() / structured_features()
        │
        ▼  ml/features/defect_features.py
49 features per commit, leakage-guarded
        │
        ▼
data/features/defect_features.csv     processed dataset
        │
        ▼
model sweep → cross-validation → held-out test → manifest + report
```

---

## 2. Label definitions and their limitations

This is the part most worth reading carefully. GitHub does not record "was this change
defective", "how urgent is this really" or "how many hours did this take", so each target
is a **derived proxy**, and each proxy has real limits.

### 2.1 `is_defective` — bug-inducing change

**Construction.** A commit *C* is labelled defective if, within a **30-day window**, a
later commit *F* exists such that:

1. `F.author != C.author` — a different person, so this is a reaction to *C*, not a
   self-correction in the same sitting;
2. `F` has **fix intent** in its subject line;
3. `F` touches **at least one path** that `C* also touched.

This is the bug-inducing-change (BIC) construction standard in just-in-time defect
prediction. Fix-intent is detected by a regex over conventional-commit prefixes
(`fix:`, `hotfix`, `bugfix`, `patch`, `revert`) and natural-language keywords
(`fix`, `bug`, `regression`, `broken`, `crash`, `traceback`, `segfault`, `npe`,
`off-by-one`, `closes #123`, …). Lockfiles, vendored trees and changelogs are excluded
from the path set, because churn there is not evidence of a defect.

**Why a proxy, not ground truth.** Version control records *what changed*, never *what
broke*. There is no field anywhere in a git repository that says "this commit introduced
a bug".

**Measured properties** (from the real run, 5 repositories, 23,784 commits):

| Property | Value |
|---|---|
| Commits examined | 23,784 |
| Fix-intent commits detected | 9,124 |
| Commits labelled defective | 6,621 |
| Positive rate | **27.84 %** |

A ~28 % positive rate is realistic for bug-inducing-change labelling: most projects spend
a meaningful share of their commits on fixes.

**Limitations, stated plainly.**

- *Silent defects count as negatives.* If a bug is never fixed in the observable window,
  or is fixed by rewriting the code so the paths never match, the commit is labelled
  negative. This inflates apparent precision.
- *Fix-intent detection is message-convention dependent.* A project that never writes
  `fix:` in commit messages contributes almost no positives; one that squashes fixes into
  unrelated commits contributes false positives.
- *Near-duplicate commits by the same author are excluded* (condition 1), which slightly
  under-counts defects caught immediately by the same person.
- *Transferability.* Trained on five popular Python/JavaScript OSS projects. Expect
  degradation on closed-source codebases, on very different domains, and on projects
  whose commit hygiene differs.

---

### 2.2 `category` — issue classification (6 classes)

`BUG`, `FEATURE_REQUEST`, `DOCUMENTATION`, `QUESTION`, `ENHANCEMENT`, `OTHER`.

**Construction.** Derived from the issue's own **real repository labels** by matching
them against a common vocabulary (`bug`, `defect`, `p0` → BUG; `enhancement`, `perf` →
ENHANCEMENT; `documentation`, `typo` → DOCUMENTATION; `question`, `how-to` → QUESTION;
`feature request`, `feat` → FEATURE_REQUEST). When no label matches, the model is
allowed to fall back on title/body wording; when neither carries signal the row is
**dropped**, not guessed. The count of dropped rows is recorded in the manifest
(`dropped_unlabelled`) and in the evaluation report.

**Limitations.** Category vocabulary is project-specific, so `P1` means different things
in different repos. Short issues with no body carry little signal. Six coarse categories
is a deliberate ceiling — the model does not attempt assignee or team routing.

---

### 2.3 `priority` — issue urgency (4 classes)

`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`.

**Construction.** Severity-style labels first (`p0`–`p4`, `sev0`–`sev4`, `critical`,
`blocker`, `security`, `regression`, …), then weak text/engagement fallbacks
(data-loss / production-down phrasing → CRITICAL; stack trace → HIGH; many comments →
HIGH/MEDIUM). Rows without any urgency signal are dropped.

**This is an estimate, not a decision.** The API response carries a `disclaimer` field
saying exactly that, and the UI renders it under every prediction. Urgency is not directly
observable in the training data, so the label is a proxy built from label vocabulary and
engagement. **Human triage remains the source of truth for priority.**

**Limitations.** Highly skewed label conventions across projects. Comment counts reflect
community size, not severity, so a popular project inflates the HIGH class.

---

### 2.4 `resolution_hours` — effort (regression)

**Construction.** `closed_at − created_at` for issues that have a real close timestamp,
clipped to `[0.02 h, 8760 h]`. This is the **time-to-close** proxy.

**Why a proxy.** GitHub records no engineering hours, no timesheets, no sprint
allocation. Time-to-close is the only effort-adjacent signal that exists in the data.

**Limitations, stated in the API response and the UI.**

- Time-to-close **includes queue and wait time** — an issue left open over a holiday or
  a release freeze inflates the target. It is a coarse proxy for hands-on effort, not a
  measurement of it.
- Heavily right-skewed; the model therefore trains on `log1p(hours)` and all reported
  metrics are computed on the original hour scale.
- Noisy for issues closed in bulk, auto-closed by bots, or blocked on third parties.

---

## 3. Feature engineering

### 3.1 Defect-risk features (49)

Grouped by what they measure. **Every historical feature is computed in a single forward
pass per repository over commits sorted by time, using only strictly prior events** — the
features for a commit at time *t* cannot observe anything at or after *t*. This is
enforced by construction and covered by a regression test
(`test_defect_features_produce_no_future_leakage`) that recomputes the matrix with extra
future commits appended and asserts the earlier rows are unchanged.

| Group | Features |
|---|---|
| **Change size** | `log_additions`, `log_deletions`, `log_churn`, `log_files`, `net_lines`, `churn_ratio`, `additions_per_file`, `deletions_per_file` |
| **Message signal** | `is_merge`, `is_bugfix`, `message_length`, `has_issue_ref`, `message_question_ratio` |
| **Timing** | `commit_hour`, `commit_weekday`, `is_weekend`, `is_business_hours` |
| **File composition** | `files_source`, `files_test`, `files_doc`, `files_generated`, `files_other`, `additions_test/source/doc`, `deletions_test/source` |
| **Author history** | `author_commit_count`, `author_defect_rate`, `author_repo_share`, `author_tenure_days`, `days_since_author_prev` |
| **Repository activity** | `repo_commits_last_7d/30d`, `repo_distinct_authors_30d`, `repo_defect_rate_30d`, `repo_churn_last_30d`, `repo_age_days`, `hours_since_repo_last` |
| **File history** | `path_change_count`, `path_defect_rate`, `path_age_days`, `paths_avg_age`, `paths_ever_touched_by_author`, `path_is_lockfile/vendor/test/doc` |

Churn features are `log1p`-scaled because line counts are heavy-tailed; a 5,000-line
refactor should not dominate a 3-line fix. Categorical rates are bounded to `[0, 1]`.

### 3.2 Text features (Models 2 & 3)

`clean_text()` normalises issue bodies before vectorisation: strips fenced and inline
code, HTML comments and tags, image embeds, URLs, e-mail addresses, quoted lines,
markdown headers/bullets/checkbox syntax, and **masks issue references** (`#1234`).
Issue references appear in nearly every issue and carry no category or priority signal —
leaving them in lets the model memorise issue numbers instead of learning language.
HTML entities are unescaped, whitespace collapsed, text lowercased.

The document is `title + title + body`: the title is repeated so a short, informative
title is not drowned out by a long body in the TF-IDF term weighting.

### 3.3 Structured features (Models 3 & 4)

22 numeric features concatenated with the TF-IDF block: `comments_count` (log and raw),
`has_comments`, `author_association_rank` (owner/member > collaborator > contributor >
first-timer), `label_count`, `has_severity_label`, `has_bug/feature/docs/question_label`,
`has_duplicate_label`, `has_wontfix_label`, `has_good_first_issue`, `length_chars`,
`length_words`, `length_sentences`, `unique_word_ratio`, `title_length`,
`has_stacktrace`, `has_critical_language`, `has_workaround`, `title_has_question`,
`is_question_like`.

Hybrid representation — sparse text block ⊕ scaled structured block in a single matrix —
lets one linear model use wording and metadata together.

---

## 4. Model selection

**Procedure, identical for all four models** (`ml/training/common.py::train_and_select`):

1. Build every candidate with fixed hyper-parameters.
2. Score each candidate with **5-fold stratified cross-validation** on the *training*
   split only, using the declared primary metric. For the regressor, 5-fold KFold on the
   training split.
3. Select the candidate with the best CV score. Ties break on the held-out metric, then
   alphabetically — the choice is deterministic.
4. Refit the winner on the full training split.
5. Report **held-out test metrics**, the confusion matrix, the per-class report, feature
   importance and the full candidate table into `manifest.json`.

The test split is **never** touched during selection.

**Primary metrics.**

| Model | Task | Primary metric | Direction |
|---|---|---|---|
| defect_risk | binary | `f1_macro` | higher |
| issue_classifier | multiclass (6) | `f1_macro` | higher |
| issue_priority | multiclass (4) | `f1_macro` | higher |
| issue_effort | regression | `mae` | **lower** |

Macro-F1 rather than accuracy because these datasets are imbalanced — plain accuracy
rewards always predicting the majority class.

**Candidate families.**

- *Defect risk:* Logistic Regression (interpretable baseline) · Random Forest ·
  Gradient Boosting · XGBoost.
- *Issue classification / priority:* word 1–2-gram TF-IDF ⊕ char\_wb 3–5-gram TF-IDF →
  Logistic Regression, and → Linear SVM wrapped in 3-fold sigmoid calibration
  (calibration is what makes a usable `predict_proba` available for the UI's
  distribution bars).
- *Effort:* Ridge on `log1p` target · Random Forest · Gradient Boosting, all on
  TF-IDF(text) ⊕ structured features with a `log1p` target transform inverted at predict
  time.

---

## 5. Evaluation

### Classification
Accuracy · precision · recall · F1 (binary: positive class; multiclass: macro and
weighted) · balanced accuracy · **confusion matrix** · per-class precision/recall/F1/
support · **ROC-AUC** (binary; One-vs-Rest macro for multiclass).

### Regression
**MAE** · **RMSE** · **R²** · median absolute error · MAPE · explained variance ·
**within-±20 %** and **within-2×** hit rate. The last two are included because hours are
not meaningfully "graded" — a 6-hour estimate is useful if it lands within a couple of
hours, and R² alone hides that.

### Uncertainty
A prediction interval is derived from the **IQR of absolute residuals on the test set**,
so the interval reflects measured error rather than an assumed distribution.

### Artefacts and reproducibility

```
ml/artifacts/<model>/v<n>/
├── model.joblib     # the fitted estimator
├── manifest.json    # data_hash, features, hyper-parameters, metrics, CV folds,
│                    #   confusion matrix, comparison table, library versions,
│                    #   target definition, limitations, trained_at
├── comparison.json  # every candidate's real metrics
└── report.json      # the report payload
ml/reports/<model>_v<n>.md        # human-readable evaluation report
ml/reports/training_summary.json  # cross-model summary
```

`data_hash` is a SHA-256 of the training frame; `library_versions` records the exact
Python, NumPy, pandas, scikit-learn and XGBoost versions. A run is reproducible if both
match. All runs are seeded (`RANDOM_SEED = 42`).

### CI gate

`tests/test_artifacts.py` fails the build if any model is untrained, undocumented
(missing target description or limitations), uncompared (fewer than two candidates, or no
selected candidate), or if a metric falls outside a plausible range — the last check
catches a hard-coded placeholder masquerading as a measurement.
