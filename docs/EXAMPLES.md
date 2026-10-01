# API Examples — copy-pasteable requests

Prerequisites: the stack is running (`docker compose up -d --build`) and models are
trained (`docker compose exec api python ml/training/run_all.py`).

```bash
BASE=http://localhost:8000/api
EMAIL="you-$(date +%s)@example.com"
```

## 1. Create an account and get a token

The **first** account created becomes an administrator.

```bash
curl -s -X POST $BASE/auth/register \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"username\":\"demo\",\"password\":\"DemoPass123\"}" \
  | jq -r .access_token > token.txt

export TOKEN=$(cat token.txt)
```

Subsequent logins:

```bash
export TOKEN=$(curl -s -X POST $BASE/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"DemoPass123"}' | jq -r .access_token)
```

## 2. Ingest a repository

```bash
curl -s -X POST $BASE/repositories/analyze \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"https://github.com/pallets/click","max_commits":2000,"max_issues":800,
       "background":false}' | jq
```

Synchronous (`background: false`) returns the row count directly. The call is idempotent —
re-running within an hour returns cached results instead of re-hitting GitHub.

```bash
# Async alternative, then poll the job
JOB=$(curl -s -X POST $BASE/repositories/analyze \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url":"psf/requests","background":true}' | jq -r .job_id)
curl -s $BASE/jobs/$JOB -H "Authorization: Bearer $TOKEN" | jq
```

## 3. Analytics

```bash
ID=$(curl -s $BASE/repositories -H "Authorization: Bearer $TOKEN" | jq -r '.items[0].id')

curl -s "$BASE/repositories/$ID/analytics" -H "Authorization: Bearer $TOKEN" \
  | jq '{total_commits, total_contributors, issue_closure_rate, pr_merge_rate,
          bugfix_ratio, days_since_last_commit}'

curl -s "$BASE/repositories/$ID/health" -H "Authorization: Bearer $TOKEN" \
  | jq '{overall_score, status, indicators: [.indicators[] | {name, score, status}]}'

curl -s "$BASE/repositories/$ID/trends?granularity=weekly&limit=12" \
  -H "Authorization: Bearer $TOKEN" | jq '.points'

curl -s "$BASE/repositories/$ID/contributors?page_size=5" \
  -H "Authorization: Bearer $TOKEN" | jq '.items[] | {github_login, commits_count}'
```

## 4. Defect risk — Model 1

```bash
curl -s -X POST $BASE/ml/defect-risk \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "repository": "pallets/click",
    "author": "Rowlando13",
    "commit_message": "Refactor parameter parsing",
    "changes": [
      {"path":"src/click/core.py","change_type":"modified","additions":180,"deletions":40},
      {"path":"tests/test_core.py","change_type":"added","additions":60,"deletions":5}
    ]
  }' | jq '{risk_level, probability, message, model_version}'
```

```
{
  "risk_level": "MEDIUM",
  "probability": 0.3629,
  "message": "MEDIUM defect risk (36%). Standard review plus a focused regression test is advisable.",
  "model_version": "defect_risk-v1"
}
```

Bands: `LOW < 0.33 ≤ MEDIUM < 0.66 ≤ HIGH`.

## 5. Issue classification — Model 2

```bash
curl -s -X POST $BASE/ml/classify-issue \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "title": "AttributeError: _Token has no attribute _name when using iter_errors",
    "body": "Traceback (most recent call last): AttributeError. Regression from 8.0.",
    "labels": ["bug", "regression"]
  }' | jq '{category, confidence, probabilities, top_features}'
```

## 6. Issue priority — Model 3

```bash
curl -s -X POST $BASE/ml/predict-priority \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "title": "Production database is corrupted after upgrade",
    "body": "We are seeing data loss. Complete outage, cannot install.",
    "labels": ["bug"], "comments_count": 6, "author_association": "MEMBER"
  }' | jq '{priority, confidence, contributing_factors, disclaimer}'
```

The `disclaimer` is always present. This is an advisory estimate; human triage decides
priority.

## 7. Effort estimation — Model 4

```bash
curl -s -X POST $BASE/ml/estimate-effort \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "title": "Implement OAuth2 device-code login flow",
    "body": "Needs a new provider, token storage and tests.",
    "labels": ["feature"]
  }' | jq '{estimated_hours, estimate_low_hours, estimate_high_hours, contributors}'
```

`methodology_note` in the response states the proxy: this is trained on time-to-close,
which includes queue time, not on engineering hours.

## 8. Batch classification

```bash
curl -s -X POST $BASE/ml/classify-batch \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"repository_id\": $ID, \"persist\": true, \"issues\": [
        {\"title\": \"#123 failing integration test\", \"body\": \"breaks on CI\"},
        {\"title\": \"#124 add parquet support\", \"body\": \"feature request\"}
      ]}" | jq '{summary, processed}'
```

## 9. Model registry and evaluation

```bash
curl -s $BASE/ml/models -H "Authorization: Bearer $TOKEN" \
  | jq '.items[] | {tag, algorithm, metrics: {f1: .metrics.f1, mae: .metrics.mae}}'

# Candidate comparison — the table behind model selection
curl -s $BASE/ml/models/comparison -H "Authorization: Bearer $TOKEN" \
  | jq '.[] | {name, selected_model, rows: [.rows[] | {model, f1, cv_f1_mean, selected}]}'

# Full evaluation report for one model
curl -s $BASE/ml/evaluation/defect_risk -H "Authorization: Bearer $TOKEN" \
  | jq '{algorithm, metrics, confusion_matrix, confusion_matrix_labels}'

# Per-class report and feature importance
curl -s $BASE/ml/evaluation/issue_classifier -H "Authorization: Bearer $TOKEN" \
  | jq '{per_class: .metrics.per_class, top: .feature_importance[:8]}'
```

## 10. Prediction audit log

```bash
curl -s "$BASE/ml/predictions?page_size=10" -H "Authorization: Bearer $TOKEN" \
  | jq '.items[] | {target_type, prediction, model_version_tag, probability, latency_ms}'
```

Every prediction records the model version that produced it and an `input_hash`, so a
result can always be traced back to the exact model and input.

## 11. Error handling

```bash
# 404 with a stable code
curl -s $BASE/repositories/999999 -H "Authorization: Bearer $TOKEN" | jq
# {"detail":"repository 999999 does not exist","code":"repository_not_found",
#  "request_id":"9f2c1a4b7e01","context":null}

# 422 naming the offending field
curl -s -X POST $BASE/ml/classify-issue \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"body":"no title"}' | jq
# {"detail":"request validation failed","code":"request_validation_error",
#  "context":{"errors":[{"field":"title","message":"Field required","type":"missing"}]}}

# 401 without a token
curl -s $BASE/repositories | jq
# {"detail":"authentication required","code":"missing_token", ...}
```

## 12. Health

```bash
curl -s $BASE/health | jq
# {"status":"healthy","version":"1.0.0","environment":"development",
#  "database":"connected","models_loaded":["defect_risk","issue_classifier",
#  "issue_priority","issue_effort"],"timestamp":"..."}

curl -s $BASE/health/github | jq
# {"authenticated":false,"rate_limit":{...},"token_fingerprint":"…"}
```

`token_fingerprint` is a hash prefix of the credential — useful for confirming *which* token
is loaded without ever exposing it.
