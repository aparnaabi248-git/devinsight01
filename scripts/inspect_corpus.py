"""Inspect the composition of the real issue corpus."""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml"))

from config import ISSUE_CORPUS

total = 0
prs = 0
issues = 0
bad = 0
repos = Counter()
labels = Counter()

with ISSUE_CORPUS.open(encoding="utf-8", errors="replace") as fh:
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
        total += 1
        is_pr = "pull_request" in obj or "/pull/" in (obj.get("html_url") or "")
        if is_pr:
            prs += 1
        else:
            issues += 1
        url = obj.get("repository_url") or ""
        if "/repos/" in url:
            parts = [p for p in url.split("/repos/", 1)[1].split("/") if p]
            if len(parts) >= 2:
                repos[f"{parts[0]}/{parts[1]}"] += 1
        for label in obj.get("labels") or []:
            if isinstance(label, dict) and label.get("name"):
                labels[label["name"]] += 1

print(f"total parsed : {total:,}")
print(f"  issues     : {issues:,}")
print(f"  pull reqs  : {prs:,}")
print(f"  malformed  : {bad:,}")
print(f"\nrepositories: {len(repos)}")
for name, count in repos.most_common(10):
    print(f"  {name:<45} {count:,}")
print(f"\ndistinct labels: {len(labels)}")
for name, count in labels.most_common(30):
    print(f"  {name:<30} {count:,}")
