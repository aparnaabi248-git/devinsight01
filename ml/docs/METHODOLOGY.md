# ML Methodology

This document explains *how* the four models are built and evaluated. For what each label
means and what it cannot see, see [`LABELS.md`](LABELS.md).

The governing rule for this project: **the target must not be a function of the features.**
Where a target could have been built from a feature, the label was rebuilt. See §3 of
`LABELS.md` for the priority model, which was rebuilt for exactly this reason.

---

## 1. Pipeline stages

| Stage | Module | Output |
|---|---|---|
| Extract | `ml/data/github_history.py` | `commits.csv`, `file_changes.csv`, `releases.csv` |
| Extract | `ml/data/issue_corpus.py` | issue DataFrame from JSONL |
| Clean | `ml/preprocessing/clean_history.py` | normalised commits, fix-intent flags, defect label |
| Clean | `ml/preprocessing/clean_text.py` | TF-IDF-ready issue text |
| Transform | `ml/preprocessing/build_datasets.py` | cached datasets + splits |
| Features | `ml/features/defect_features.py` | 49 leakage-guarded change features |
| Features | `ml/features/issue_features.py` | category/priority labels, 22 structured features |
| Train | `ml/training/*.py` | model sweep, CV, held-out metrics |
| Evaluate | `ml/evaluation/*` | metrics, confusion matrix, reports |
| Save | `ml/training/common.py::save_artifact` | `model.joblib` + `manifest.json` |
| Serve | `ml/inference/*` | `predictor.py`, `registry.py` |

Each stage caches to disk, so re-running a later stage does not repeat the earlier work.

---

## 2. Feature engineering

### 2.1 Defect risk — 49 features

| Group | Features |
|---|---|
| Change size | `log_additions`, `log_deletions`, `log_churn`, `log_files`, `net_lines`, `churn_ratio`, `additions_per_file`, `deletions_per_file` |
| Message | `is_merge`, `is_bugfix`, `message_length`, `has_issue_ref`, `message_question_ratio` |
| Timing | `commit_hour`, `commit_weekday`, `is_weekend`, `is_business_hours` |
| File composition | `files_source`, `files_test`, `files_doc`, `files_generated`, `files_other`, `additions_test/source/doc`, `deletions_test/source` |
| Author history | `author_commit_count`, `author_defect_rate`, `author_repo_share`, `author_tenure_days`, `days_since_author_prev` |
| Repository activity | `repo_commits_last_7d/30d`, `repo_distinct_authors_30d`, `repo_defect_rate_30d`, `repo_churn_last_30d`, `repo_age_days`, `hours_since_repo_last` |
| File history | `path_change_count`, `path_defect_rate`, `path_age_days`, `paths_avg_age`, `paths_ever_touched_by_author`, `path_is_lockfile/vendor/test/doc` |

**Leakage control.** Historical features are computed in one forward pass per repository
over commits sorted by time, reading only strictly prior events. A commit's features cannot
observe anything at or after it. Asserted by
`test_defect_features_produce_no_future_leakage`, which recomputes the matrix with extra
future commits appended and checks the earlier rows are unchanged.

**Heavy tails.** Line counts are `log1p`-scaled — a 5,000-line refactor should not dominate
a 3-line fix. Rates are bounded to `[0, 1]`. NaNs are median-imputed within the frame.

**Noise filtering.** Lockfiles, vendored trees, generated artefacts and changelogs are
excluded from the label's path set and flagged rather than modelled.

### 2.2 Text representation

`clean_text()` strips fenced and inline code, HTML comments and tags, image embeds, URLs,
e-mail addresses, quoted lines, markdown headers/bullets/checkbox syntax, and **masks issue
references** (`#1234` appears in nearly every issue and carries no signal — leaving it in
lets the model memorise numbers). Entities are unescaped, whitespace collapsed, text
lowercased.

The document is `title + title + body`: the title is repeated so a short informative title
is not drowned out by a long body under TF-IDF term weighting.

Both word 1–2-gram and `char_wb` 3–5-gram views are combined via `FeatureUnion` — word
n-grams capture vocabulary, character n-grams capture morphology, misspellings and code
identifiers. They must run in *parallel*: a `Pipeline` would feed the first vectoriser's
sparse matrix into the second.

### 2.3 Structured features

22 numeric features, scaled and hstacked with the TF-IDF block: `comments_count` (log and
raw), `has_comments`, `author_association_rank`, `label_count`, `has_severity_label`,
`has_bug/feature/docs/question_label`, `has_duplicate_label`, `has_wontfix_label`,
`has_good_first_issue`, `length_chars/words/sentences`, `unique_word_ratio`, `title_length`,
`has_stacktrace`, `has_critical_language`, `has_workaround`, `title_has_question`,
`is_question_like`.

Implemented as `HybridTextClassifier` / `HybridTextRegressor` in `ml/models/hybrid.py` —
genuine scikit-learn estimators, so they work with `cross_val_predict`, `clone` and
`joblib` unmodified. `StandardScaler(with_mean=False)` is required because the structured
block lives inside a sparse matrix.

---

## 3. Model selection

`ml/training/common.py::train_and_select` runs the same procedure for all four models:

1. Build every candidate with fixed hyper-parameters.
2. Score each with **5-fold stratified cross-validation on the training split only**.
3. Select the best CV score; ties break on the held-out metric, then alphabetically —
   deterministic.
4. Refit the winner on the full training split.
5. Report held-out test metrics, confusion matrix, per-class report, feature importance and
   the full candidate table into `manifest.json`.

The test split is never touched during selection.

| Model | Task | Primary metric | Direction | Candidates |
|---|---|---|---|---|
| defect_risk | binary | `f1_macro` | higher | Logistic Regression, Random Forest, Gradient Boosting, XGBoost |
| issue_classifier | multiclass (6) | `f1_macro` | higher | TF-IDF⊕char → Logistic Regression, → calibrated Linear SVM |
| issue_priority | multiclass (4) | `f1_macro` | higher | same two, on TF-IDF⊕structured |
| issue_effort | regression | `mae` | **lower** | Ridge, Random Forest, Gradient Boosting (on `log1p` target) |

Macro-F1 rather than accuracy because every dataset is imbalanced — accuracy rewards always
predicting the majority class.

**A candidate that fails is reported and skipped, never silently dropped**, and the sweep
fails loudly if *every* candidate fails.

---

## 4. Evaluation

**Classification:** accuracy, precision, recall, F1 (binary: positive class; multiclass:
macro + weighted), balanced accuracy, confusion matrix, per-class precision/recall/F1/
support, ROC-AUC (binary; OvR macro for multiclass).

**Regression:** MAE, RMSE, R², median absolute error, MAPE, explained variance,
**within-±20 %** and **within-2×** hit rate. The last two matter because hours are not
meaningfully graded — a 6-hour estimate is useful if it lands within a couple of hours, and
R² alone hides that.

**Prediction intervals** come from the IQR of absolute residuals on the test set, so the
interval reflects measured error rather than an assumed distribution.

---

## 5. Artefacts and reproducibility

```
ml/artifacts/<model>/v<n>/
├── model.joblib     # fitted estimator
├── manifest.json    # data_hash, features, hyper-parameters, metrics, CV folds,
│                    #   confusion matrix, candidate comparison, library versions,
│                    #   target definition, limitations, trained_at
├── comparison.json  # every candidate's real metrics
└── report.json      # the report payload
ml/reports/<model>_v<n>.md        # human-readable evaluation report
ml/reports/training_summary.json  # cross-model summary
```

`data_hash` is a SHA-256 of the training frame; `library_versions` records the exact Python,
NumPy, pandas, scikit-learn and XGBoost versions. A run is reproducible if both match. All
runs are seeded (`RANDOM_SEED = 42`).

**MLflow.** With `MLFLOW_TRACKING_ENABLED=true`, every run logs params (data version, CV
folds, seed, feature count, library versions), per-candidate CV and test metrics, fit time,
and the model artifact. Tracking is strictly opt-in — an unreachable MLflow server must
never stall a training run.

---

## 6. CI quality gate

`tests/test_artifacts.py` fails the build if any model is:

- untrained, or missing `model.joblib` / `manifest.json`;
- **undocumented** — no target description or no limitations text;
- **uncompared** — fewer than two candidates, or no candidate marked selected;
- missing a recorded training time or library versions;
- reporting a metric outside a plausible range.

That last check is the one that catches a hard-coded placeholder masquerading as a
measurement.
