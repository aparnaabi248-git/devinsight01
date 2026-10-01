# Architecture Diagram

Mermaid source for the figures in `README.md` and `docs/ARCHITECTURE.md`.

## 1. System context

```mermaid
flowchart TB
  user([Engineer / Manager]) -->|HTTPS + JWT| web

  subgraph edge["Edge"]
    web[React 18 + TypeScript<br/>Vite · TanStack Query · Recharts]
  end

  subgraph app["Application"]
    api[FastAPI<br/>routers · Pydantic v2 schemas]
    auth[JWT + bcrypt<br/>analyst / admin role gate]
    gh[GitHubClient<br/>ETag cache · backoff · rate limit]
    mls[ML Service<br/>inference + provenance]
  end

  subgraph state["State"]
    db[(PostgreSQL 16<br/>19 normalised tables)]
    art[(Model registry<br/>joblib + manifests)]
    raw[(Raw store<br/>git clones · JSONL)]
    mlf[(MLflow<br/>runs · metrics · artifacts)]
  end

  github[(GitHub<br/>REST API + git transport)]

  web --> api
  api --> auth
  api --> gh
  api --> mls
  gh --> db
  api --> db
  mls --> art
  mls --> db
  raw --> db
  art --> mls
  gh <--> github
```

## 2. Data pipeline

```mermaid
flowchart LR
  subgraph extract["EXTRACT"]
    git[(git clone<br/>5 real repos)]
    corpus[(public issue<br/>corpus JSONL)]
  end

  subgraph store["Raw store"]
    rawd[(data/raw)]
  end

  subgraph clean["CLEAN · ml/preprocessing"]
    ch[clean_history<br/>fix-intent · authorship]
    ct[clean_text<br/>code · HTML · URLs · issue refs]
  end

  subgraph label["LABEL"]
    l1[bug-inducing-change proxy]
    l2[category from label vocabulary]
    l3[priority from labels ONLY]
    l4[resolution_hours proxy]
  end

  subgraph features["FEATURES · ml/features"]
    f1[49 change features<br/>leakage-guarded]
    f2[TF-IDF word+char]
    f3[22 structured features]
  end

  subgraph train["TRAIN · ml/training"]
    sweep[Candidate sweep]
    cv[5-fold stratified CV]
    sel[Select on CV]
    test[Held-out test metrics]
  end

  out[(artifacts + reports)]

  git --> rawd
  corpus --> rawd
  rawd --> ch
  rawd --> ct
  ch --> l1
  ch --> l2
  ch --> l3
  ch --> l4
  ct --> f2
  l1 --> f1
  l2 --> f3
  l3 --> f3
  f1 --> sweep
  f2 --> sweep
  f3 --> sweep
  sweep --> cv --> sel --> test --> out
```

## 3. Leakage guard

```mermaid
flowchart LR
  t0["commit @ t"] --> f1["author_commit_count<br/>prior commits only"]
  t0 --> f2["repo_commits_last_30d<br/>prior window only"]
  t0 --> f3["path_change_count<br/>prior changes only"]
  future["later commits @ t+1 …"] -.->|never read| f1
  future -.->|never read| f2
  future -.->|never read| f3

  style future stroke-dasharray: 5 5
```

## 4. Model lifecycle

```mermaid
flowchart LR
  data[(Dataset + data_hash)] --> train[Train candidates]
  train --> cv{CV score}
  cv -->|best| refit[Refit winner]
  refit --> eval[Held-out evaluation]
  eval --> manifest[(manifest.json<br/>metrics · features · limits)]
  eval --> report[(reports/*.md)]
  eval --> registry[(model registry)]
  manifest -.->|MLflow run| mlf[(MLflow)]
  registry --> serve[Inference API]
  serve --> pred[(predictions<br/>model version + input hash)]
  gate[[tests/test_artifacts.py]] -.->|fails CI on a
     placeholder metric| manifest
```

## 5. Deployment topology

```mermaid
flowchart TB
  net([Internet]) --> proxy[TLS terminator<br/>nginx]

  subgraph prod["Production host"]
    proxy --> webc[web: nginx + SPA]
    proxy --> apic[api: uvicorn<br/>non-root]
    subgraph dbv["PostgreSQL 16"]
      d[(devinsight)]
    end
    apic --> d
    mfc[mlflow:5000]
    apic -.->|experiment tracking| mfc
  end

  subgraph storage["Persistent volumes"]
    v1[(db_data)]
    v2[(etl_data)]
    v3[(ml_artifacts)]
    v4[(mlflow_data)]
  end

  d --- v1
  apic --- v2
  apic --- v3
  mfc --- v4
```
