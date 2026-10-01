"""EXTRACTION — the real GitHub issue corpus (JSONL of verbatim REST API issue objects)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from config import ISSUE_CORPUS, ISSUE_CORPUS_SOURCE


def _repo_from_api_url(url: str | None) -> str | None:
    if not url:
        return None
    marker = "/repos/"
    if marker in url:
        tail = url.split(marker, 1)[1]
        parts = [p for p in tail.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return None


def load_issue_corpus(
    path: Path | str = ISSUE_CORPUS, include_pull_requests: bool = True
) -> pd.DataFrame:
    """Load the real issue corpus into a DataFrame with real labels and timestamps.

    GitHub's REST API models a pull request as an issue with extra fields, and public
    mirrors of the `/issues` endpoint therefore contain mostly pull requests. PR titles,
    bodies, labels and timestamps are genuine issue-shaped text, so they are kept by
    default and flagged via `is_pull_request` rather than discarded. Pass
    `include_pull_requests=False` for issues only.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `python ml/data/acquire.py` to download the corpus."
        )

    rows: list[dict] = []
    bad = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not isinstance(obj, dict) or "number" not in obj:
                bad += 1
                continue
            is_pr = "pull_request" in obj or "/pull/" in (obj.get("html_url") or "")
            if is_pr and not include_pull_requests:
                continue

            labels = [
                (l or {}).get("name")
                for l in (obj.get("labels") or [])
                if isinstance(l, dict) and l.get("name")
            ]
            rows.append(
                {
                    "issue_id": obj.get("id"),
                    "number": obj.get("number"),
                    "repository": _repo_from_api_url(obj.get("repository_url")),
                    "title": obj.get("title") or "",
                    "body": obj.get("body") or "",
                    "state": obj.get("state"),
                    "labels": labels,
                    "label_str": " ".join(sorted({l.lower() for l in labels})),
                    "comments_count": len(obj.get("comments") or [])
                    or int(obj.get("comments") or 0),
                    "author_login": (obj.get("user") or {}).get("login"),
                    "author_association": obj.get("author_association"),
                    "assignee_login": (obj.get("assignee") or {}).get("login"),
                    "created_at": obj.get("created_at"),
                    "updated_at": obj.get("updated_at"),
                    "closed_at": obj.get("closed_at"),
                    "html_url": obj.get("html_url"),
                    "is_pull_request": is_pr,
                }
            )

    if bad:
        print(f"  [corpus] skipped {bad} malformed line(s)")
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError(
            f"{path} yielded no usable records — expected verbatim GitHub issue objects."
        )
    pr_count = int(df["is_pull_request"].sum())
    print(
        f"  [corpus] loaded {len(df):,} real records from {path.name} "
        f"({df['repository'].nunique()} repository/repositories, "
        f"{pr_count:,} pull requests, {len(df) - pr_count:,} issues)"
    )
    return df


def corpus_provenance() -> dict[str, str]:
    return {
        "corpus_file": str(ISSUE_CORPUS),
        "corpus_source": ISSUE_CORPUS_SOURCE,
        "record_note": (
            "GitHub models a pull request as an issue with extra fields, so public "
            "mirrors of the /issues endpoint are predominantly pull requests. Their "
            "titles, bodies, labels and timestamps are genuine issue-shaped text and are "
            "retained; the is_pull_request flag records the distinction."
        ),
    }
