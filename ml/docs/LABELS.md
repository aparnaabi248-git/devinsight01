# Label Definitions and Their Limitations

GitHub does not record whether a change was defective, how urgent an issue really is, or
how many hours an issue took. Every target in DevInsight is therefore a **derived proxy**,
and every proxy has limits. This document records what each label is, how it was built,
and — more importantly — what it cannot see.

The headline rule: **the target must not be a function of a feature.** If the target can be
computed from the inputs, the model learns to reproduce our own rule and the reported score
measures nothing. Two of these labels were rebuilt after an initial version violated that
rule; both cases are documented below.

---

## 1. `is_defective` — bug-inducing change

**Unit of analysis:** one commit.
**Window:** 30 days.

A commit *C* is positive if a later commit *F* exists where:

1. `F.author != C.author` — a different person reacted to it;
2. *F*'s subject line carries **fix intent**;
3. *F* touches **at least one path** that *C* touched.

This is the bug-inducing-change (BIC) construction from the just-in-time
defect-prediction literature.

**Fix intent** is detected by regex over conventional-commit prefixes (`fix:`,
`hotfix`, `bugfix`, `patch`, `revert`) and keywords (`fix`, `bug`, `regression`,
`broken`, `crash`, `traceback`, `segfault`, `npe`, `off-by-one`, `closes #123`, …).
Lockfiles, vendored trees, generated artefacts and changelogs are excluded from the
path set — churn there is not evidence of a defect.

### Measured on the real data

| Property | Value |
|---|---|
| Repositories | 5 (Python + JavaScript) |
| Commits examined | 23,784 |
| File changes | 41,358 |
| Fix-intent commits detected | 9,124 |
| Commits labelled defective | 6,621 |
| **Positive rate** | **27.84 %** |
| Positive rate, train split | 27.91 % |
| Positive rate, test split | 27.44 % |

### Limitations

- **Silent defects count as negatives.** A bug never fixed in the window, or fixed by a
  rewrite where no path matches, is labelled negative. This inflates apparent precision —
  the model is partly learning "was someone else forced to touch this file again soon".
- **Fix-intent detection is message-convention dependent.** A project that never writes
  `fix:` contributes almost no positives; one that folds fixes into unrelated commits
  contributes false positives.
- **Same-author corrections are excluded** (condition 1), so defects caught immediately by
  the author are missed.
- **Temporal rather than ideal split.** The holdout is the most recent 15 %, which is the
  realistic deployment scenario but means the test set spans a narrower feature
  distribution than a random split would.
- **Transferability is untested.** Five popular Python/JavaScript OSS projects do not
  predict performance on a closed-source codebase.

---

## 2. `category` — issue classification

`BUG` · `FEATURE_REQUEST` · `DOCUMENTATION` · `QUESTION` · `ENHANCEMENT` · `OTHER`

**Construction.** Match the issue's own repository labels against a common vocabulary.
When no label matches, fall back on title/body wording. When neither carries signal the row
is **dropped**, not guessed — the drop count is recorded as `dropped_unlabelled` in the
manifest so the reader knows exactly how much of the corpus was excluded and why.

### Measured

| Property | Value |
|---|---|
| Corpus records loaded | 3,569 |
| Records with a derivable category | 1,900 |
| Dropped (no derivable signal) | 1,669 |
| Class distribution | FEATURE_REQUEST 828 · BUG 562 · ENHANCEMENT 234 · DOCUMENTATION 141 · QUESTION 135 |

### Limitations

- **Project-specific vocabulary.** The mapping from label name to category is defined by
  *this* project's maintainers. Another project may use the same word differently.
- **The text fallback is a weak, hand-written rule.** It catches obviously-worded issues;
  the reported score is a mix of "learned the label vocabulary" and "learned our regexes".
  Rows where the fallback was used are not separable after the fact, so the honest read is
  a lower bound on generalisation.
- **Single project.** The corpus is one repository. Expect material degradation elsewhere.
- **Short issues carry little signal.** A title-only issue is often genuinely ambiguous.
- `OTHER` absorbs everything the vocabulary misses, which makes it a heterogeneous bucket.

---

## 3. `priority` — issue urgency

`CRITICAL` · `HIGH` · `MEDIUM` · `LOW`

### Why this label was rebuilt

The first implementation derived priority from the issue's **text** (data-loss phrasing →
CRITICAL, stack trace → HIGH) and its **comment count**. Both are also supplied to the
model as *features*. The consequence was a CV macro-F1 of **0.947** — a meaningless
number: the model was learning to reproduce our own regexes, and the evaluation was
measuring the rule rather than the task.

The label is now **label-only**: it reads the human-applied labels and nothing else.
Because the corpus contains no `p0`/`sev0`-style severity labels, the mapping is from the
label *semantics* maintainers actually use (`bug`, `dataset bug`, `enhancement`,
`documentation`, `question`, `good first issue`, `wontfix`, `duplicate`, the
`*-viewer`/`*-request`/`*-discussion` family) onto the four levels.

Text and comment count are still available as **features** — that is the legitimate
version of this task: *given the issue content, predict the triage decision a human
already made*.

### Limitations

- **Still a proxy.** A genuine sev-1 outage labelled only `bug` is indistinguishable from a
  typo labelled `bug`. The model learns the project's triage conventions, not impact.
- **Within-project only.** The reported score measures within-project predictability. It
  is **not** a general triage accuracy, and must not be presented as one.
- **Label drift.** Maintainers change triage habits over time; the model does not.
- **The API always returns a disclaimer** and the UI renders it. This is an advisory signal.
  Human triage is the source of truth.

---

## 4. `resolution_hours` — development effort

**Construction.** `closed_at − created_at`, clipped to `[0.02 h, 8760 h]`.

**Why a proxy.** GitHub records no engineering hours. Time-to-close is the only
effort-adjacent signal in the data.

### Measured

| Property | Value |
|---|---|
| Mean target | 255.5 h |
| Median target | 41.8 h |
| 90th percentile | 533.3 h |
| Train / test rows | 1,950 / 345 |

The distribution is what you would expect: a median of ~42 hours and a mean of ~255 hours
means a small number of issues stayed open for months and dominate the average.

### Limitations

- **Includes queue and wait time.** An issue left open over a release freeze or a holiday
  inflates the target. This is an *upper* bound on hands-on effort, not a measurement of it.
- **Right-skewed**, which is why the model trains on `log1p(hours)`; all reported metrics
  are on the original hour scale.
- **Bulk-closed and bot-closed issues** are pure noise in the target.
- **Blocked issues** carry third-party wait time the team cannot control.
- A low R² on this task is expected and honest: much of the variance in time-to-close is
  scheduling, not difficulty.

---

## Reproducing the labels

```bash
python ml/data/acquire.py                 # real repositories + issue corpus
cd ml && python training/run_all.py        # rebuilds every label and retrains
```

Each manifest records the resulting class distribution, the drop counts, the data hash and
the limitations text, so any run is auditable without re-reading this document.
